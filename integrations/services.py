"""Integration service helpers for connection checks and Shopify sync."""

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportAttributeAccessIssue=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportUnusedFunction=false, reportUnusedVariable=false, reportUnnecessaryCast=false

from datetime import datetime, timedelta
from decimal import Decimal
import time
from typing import Any, cast
from urllib.parse import urlparse
from xmlrpc import client as xmlrpc_client
import base64
import hashlib
import hmac
import logging

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from products.models import Product, Category, ProductBundle, Market

from .models import Integration, OdooCredentials, QuickBooksCredentials, ShopifyWebhookDelivery

logger = logging.getLogger(__name__)

# Intuit OAuth / API constants
INTUIT_TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
INTUIT_AUTH_URL = 'https://appcenter.intuit.com/connect/oauth2'


def _test_shopify(integration: Integration):
    creds = getattr(integration, 'shopify_credentials', None)
    if not creds:
        return False, 'Shopify credentials not configured.'

    endpoint = f"{creds.store_url.rstrip('/')}/admin/api/{creds.api_version}/shop.json"
    try:
        response = requests.get(
            endpoint,
            headers={
                'X-Shopify-Access-Token': creds.access_token,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return False, f'Shopify connection failed: {exc}'

    if response.status_code == 200:
        return True, 'Shopify connection succeeded.'
    return False, f'Shopify connection failed with status {response.status_code}.'


def _test_odoo(integration: Integration):
    creds = getattr(integration, 'odoo_credentials', None)
    if not creds:
        return False, 'Odoo credentials not configured.'

    try:
        transport = _OdooTimeoutTransport(timeout=15)
        common = xmlrpc_client.ServerProxy(
            f"{creds.server_url.rstrip('/')}/xmlrpc/2/common",
            transport=transport,
        )
        uid = common.authenticate(
            creds.database_url,
            creds.email,
            creds.api_key,
            {},
        )
    except (xmlrpc_client.Fault, xmlrpc_client.ProtocolError, OSError) as exc:
        return False, f'Odoo connection failed: {exc}'

    if uid:
        return True, 'Odoo connection succeeded.'
    return False, 'Odoo authentication failed. Check database/email/API key.'


def _ensure_quickbooks_token(creds: QuickBooksCredentials) -> str:
    """Return a valid access token, refreshing if expired.

    Uses select_for_update() to prevent concurrent refresh races — Intuit
    rotates refresh tokens, so two simultaneous refreshes would invalidate
    each other.
    """
    # Fast path: token still valid (with 5-min safety buffer)
    if (
        creds.access_token
        and creds.token_expiry
        and timezone.now() < creds.token_expiry - timedelta(minutes=5)
    ):
        return creds.access_token

    if not creds.refresh_token:
        raise ValueError(
            'QuickBooks OAuth tokens are missing. '
            'Please reconnect via the QuickBooks authorization flow.'
        )

    # Lock the row to prevent concurrent refreshes
    with transaction.atomic():
        locked_creds = QuickBooksCredentials.objects.select_for_update().get(pk=creds.pk)

        # Double-check: another thread may have refreshed while we waited
        if (
            locked_creds.access_token
            and locked_creds.token_expiry
            and timezone.now() < locked_creds.token_expiry - timedelta(minutes=5)
        ):
            # Sync the caller's reference
            creds.access_token = locked_creds.access_token
            creds.token_expiry = locked_creds.token_expiry
            return locked_creds.access_token

        auth_header = base64.b64encode(
            f'{locked_creds.client_id}:{locked_creds.client_key}'.encode()
        ).decode()

        try:
            resp = requests.post(
                INTUIT_TOKEN_URL,
                headers={
                    'Authorization': f'Basic {auth_header}',
                    'Accept': 'application/json',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': locked_creds.refresh_token,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            raise ValueError(f'QuickBooks token refresh request failed: {exc}')

        if resp.status_code >= 400:
            logger.error(
                'QuickBooks token refresh failed: status=%d body=%s',
                resp.status_code,
                resp.text[:500],
            )
            raise ValueError(
                'QuickBooks token refresh failed. Please reconnect via the authorization flow.'
            )

        data = resp.json()
        locked_creds.access_token = data['access_token']
        locked_creds.refresh_token = data.get('refresh_token', locked_creds.refresh_token)
        locked_creds.token_expiry = timezone.now() + timedelta(
            seconds=data.get('expires_in', 3600)
        )
        locked_creds.save(update_fields=['access_token', 'refresh_token', 'token_expiry'])

        # Sync the caller's in-memory reference
        creds.access_token = locked_creds.access_token
        creds.refresh_token = locked_creds.refresh_token
        creds.token_expiry = locked_creds.token_expiry

    return creds.access_token


def _quickbooks_request(
    method: str,
    url: str,
    creds: QuickBooksCredentials,
    *,
    json_payload: dict[str, Any] | None = None,
    max_retries: int = 2,
) -> requests.Response:
    """Make a QuickBooks API request with automatic token refresh and retry.

    Returns the response object even on exhaustion (so callers can inspect
    status codes). Raises ValueError only on connection errors.
    """
    backoff = 1.0
    max_backoff = 2.0  # Cap to avoid blocking the web worker too long
    last_exc: Exception | None = None
    last_resp: requests.Response | None = None

    for attempt in range(max_retries):
        token = _ensure_quickbooks_token(creds)
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }

        try:
            resp = requests.request(
                method,
                url,
                headers=headers,
                json=json_payload,
                timeout=30,
            )
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning(
                'QuickBooks request failed (attempt %d/%d): %s',
                attempt + 1, max_retries, exc,
            )
            time.sleep(min(backoff, max_backoff))
            backoff *= 2
            continue

        last_resp = resp

        if resp.status_code == 401 and attempt < max_retries - 1:
            # Token may have been revoked or expired; force refresh.
            creds.token_expiry = None
            creds.save(update_fields=['token_expiry'])
            logger.info('QuickBooks 401 — forcing token refresh (attempt %d)', attempt + 1)
            time.sleep(0.5)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            logger.warning(
                'QuickBooks %d — retrying (attempt %d/%d)',
                resp.status_code, attempt + 1, max_retries,
            )
            time.sleep(min(backoff, max_backoff))
            backoff *= 2
            continue

        return resp

    # Retries exhausted: return the last response if we have one (lets caller
    # inspect status_code), otherwise raise on connection error.
    if last_resp is not None:
        return last_resp
    raise ValueError(f'QuickBooks request failed after {max_retries} attempts: {last_exc}')


def _test_quickbooks(integration: Integration):
    creds = getattr(integration, 'quickbooks_credentials', None)
    if not creds:
        return False, 'QuickBooks credentials not configured.'

    if not creds.client_id or not creds.client_key or not creds.realm_id:
        return False, 'QuickBooks credentials are incomplete (missing client_id, client_key, or realm_id).'

    if not creds.access_token:
        return False, 'QuickBooks not connected. Please complete the OAuth authorization flow.'

    try:
        endpoint = f'{creds.api_base_url}/v3/company/{creds.realm_id}/companyinfo/{creds.realm_id}'
        resp = _quickbooks_request('GET', endpoint, creds)

        if resp.status_code == 200:
            body = resp.json()
            company_info = body.get('CompanyInfo', {})
            company_name = company_info.get('CompanyName', 'Unknown')
            return True, f'Connected to QuickBooks company: {company_name}'
        if resp.status_code == 401:
            return False, 'Not connected (OAuth tokens expired or revoked). Please reconnect.'
        return False, f'Integration failed: QuickBooks returned status {resp.status_code}'
    except ValueError as exc:
        return False, str(exc)

def _qb_check_response(resp: requests.Response, label: str) -> None:
    """Raise a descriptive ValueError when a QuickBooks API call fails."""
    if resp.status_code == 200:
        return
    try:
        body = resp.json()
        fault = body.get('Fault', {})
        errors = fault.get('Error', [])
        msg = '; '.join(
            f"{e.get('Message', '')} (code {e.get('code', '')})" for e in errors
        ) if errors else resp.text[:300]
    except Exception:
        msg = resp.text[:300]
    raise ValueError(
        f'QuickBooks {label} request failed (HTTP {resp.status_code}): {msg}'
    )


def _ensure_qb_environment(creds: 'QuickBooksCredentials') -> None:
    """Detect and fix the QB environment (SANDBOX vs PRODUCTION) by probing both API endpoints.

    QB returns 403 ApplicationAuthorizationFailed when the access token is for a different
    environment than the one configured (e.g. production token + sandbox base URL). This helper
    silently corrects the environment so that all subsequent API calls use the right base URL.
    """
    if not creds.realm_id:
        return

    # Ensure we probe with a fresh (non-expired) token. If refresh fails (no refresh token),
    # bail out — the caller's _quickbooks_request will surface the real error.
    try:
        access_token = _ensure_quickbooks_token(creds)
    except ValueError:
        return

    env_map = [
        ('PRODUCTION', 'https://quickbooks.api.intuit.com'),
        ('SANDBOX', 'https://sandbox-quickbooks.api.intuit.com'),
    ]

    # Try the currently configured environment first — skip the probe if it's already correct.
    if creds.environment == 'SANDBOX':
        env_map = list(reversed(env_map))

    for env_value, base_url in env_map:
        try:
            probe = requests.get(
                f'{base_url}/v3/company/{creds.realm_id}/companyinfo/{creds.realm_id}',
                headers={
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json',
                },
                timeout=10,
            )
            if probe.status_code == 200:
                if creds.environment != env_value:
                    logger.info(
                        'QB environment mismatch detected: was %s, correcting to %s for integration %s',
                        creds.environment, env_value, creds.integration_id,
                    )
                    creds.environment = env_value
                    creds.save(update_fields=['environment'])
                return
        except requests.RequestException:
            continue


def fetch_quickbooks_options(integration: Integration) -> dict[str, list[dict[str, str]]]:
    creds = getattr(integration, 'quickbooks_credentials', None)
    if not creds:
        raise ValueError('QuickBooks credentials not configured.')
    if not creds.access_token:
        raise ValueError('QuickBooks not connected. Please complete the OAuth authorization flow.')
    if not creds.realm_id:
        raise ValueError('QuickBooks Realm ID is missing. Please reconnect via the OAuth flow.')

    _ensure_qb_environment(creds)

    from urllib.parse import quote

    # 1. Fetch Customers
    cust_query = quote('SELECT Id, DisplayName FROM Customer WHERE Active = true MAXRESULTS 1000')
    cust_url = f'{creds.api_base_url}/v3/company/{creds.realm_id}/query?query={cust_query}&minorversion=75'
    cust_resp = _quickbooks_request('GET', cust_url, creds)
    _qb_check_response(cust_resp, 'Customers')

    customers = []
    data = cust_resp.json().get('QueryResponse', {})
    for c in data.get('Customer', []):
        customers.append({
            'id': str(c.get('Id', '')),
            'name': str(c.get('DisplayName', '')),
        })

    # 2. Fetch Tax Codes
    tax_query = quote('SELECT Id, Name, Description FROM TaxCode WHERE Active = true MAXRESULTS 1000')
    tax_url = f'{creds.api_base_url}/v3/company/{creds.realm_id}/query?query={tax_query}&minorversion=75'
    tax_resp = _quickbooks_request('GET', tax_url, creds)
    _qb_check_response(tax_resp, 'TaxCodes')

    tax_codes = []
    data = tax_resp.json().get('QueryResponse', {})
    for t in data.get('TaxCode', []):
        name = str(t.get('Name', ''))
        desc = str(t.get('Description', ''))
        display = f'{name} - {desc}' if desc and desc != name else name
        tax_codes.append({
            'id': str(t.get('Id', '')),
            'name': display,
        })

    # 3. Fetch Items (products) for Default Product dropdown
    item_query = quote('SELECT Id, Name, Type FROM Item WHERE Active = true MAXRESULTS 1000')
    item_url = f'{creds.api_base_url}/v3/company/{creds.realm_id}/query?query={item_query}&minorversion=75'
    item_resp = _quickbooks_request('GET', item_url, creds)
    _qb_check_response(item_resp, 'Items')

    items = []
    data = item_resp.json().get('QueryResponse', {})
    for i in data.get('Item', []):
        item_type = str(i.get('Type', ''))
        name = str(i.get('Name', ''))
        display = f'{name} ({item_type})' if item_type else name
        items.append({
            'id': str(i.get('Id', '')),
            'name': display,
        })

    return {
        'customers': sorted(customers, key=lambda x: x['name'].lower()),
        'tax_codes': sorted(tax_codes, key=lambda x: x['name'].lower()),
        'items': sorted(items, key=lambda x: x['name'].lower()),
    }


def fetch_odoo_options(integration: Integration) -> dict[str, list[dict[str, str]]]:
    """Fetch partners and products from Odoo for the config modal dropdowns."""
    creds = getattr(integration, 'odoo_credentials', None)
    if not creds:
        raise ValueError('Odoo credentials not configured.')

    base_url = creds.server_url.rstrip('/')
    transport = _OdooTimeoutTransport(timeout=30)
    common = xmlrpc_client.ServerProxy(f'{base_url}/xmlrpc/2/common', transport=transport)

    try:
        uid = cast(
            int | bool,
            common.authenticate(creds.database_url, creds.email, creds.api_key, {}),
        )
    except (xmlrpc_client.Fault, xmlrpc_client.ProtocolError, OSError) as exc:
        raise ValueError(f'Odoo authentication failed: {exc}') from exc

    if not uid:
        raise ValueError('Odoo authentication failed. Check credentials.')

    models_proxy = xmlrpc_client.ServerProxy(f'{base_url}/xmlrpc/2/object', transport=transport)
    uid_int = cast(int, uid)

    company_id = int(creds.company_id) if creds.company_id else None

    # 1. Fetch Partners (customers)
    partners = []
    try:
        partner_domain = [['customer_rank', '>', 0]]
        if company_id:
            partner_domain.append(['company_id', 'in', [company_id, False]])
        partner_ids = cast(
            list[int],
            _odoo_execute(
                models_proxy, creds.database_url, uid_int, creds.api_key,
                'res.partner', 'search',
                [partner_domain],
                {'limit': 500},
            ),
        )
        if partner_ids:
            partner_records = cast(
                list[dict[str, Any]],
                _odoo_execute(
                    models_proxy, creds.database_url, uid_int, creds.api_key,
                    'res.partner', 'read',
                    [partner_ids],
                    {'fields': ['id', 'name']},
                ),
            )
            for p in partner_records:
                partners.append({
                    'id': str(p.get('id', '')),
                    'name': str(p.get('name', '')),
                })
    except Exception:  # noqa: BLE001
        logger.warning('Failed to fetch Odoo partners for integration %s', integration.id)

    # 2. Fetch Products (product.product)
    products = []
    try:
        product_ids = cast(
            list[int],
            _odoo_execute(
                models_proxy, creds.database_url, uid_int, creds.api_key,
                'product.product', 'search',
                [[['active', '=', True]]],
                {'limit': 500},
            ),
        )
        if product_ids:
            product_records = cast(
                list[dict[str, Any]],
                _odoo_execute(
                    models_proxy, creds.database_url, uid_int, creds.api_key,
                    'product.product', 'read',
                    [product_ids],
                    {'fields': ['id', 'name', 'default_code']},
                ),
            )
            for pr in product_records:
                name = str(pr.get('name', ''))
                sku = str(pr.get('default_code', '') or '')
                display = f'{name} [{sku}]' if sku else name
                products.append({
                    'id': str(pr.get('id', '')),
                    'name': display,
                })
    except Exception:  # noqa: BLE001
        logger.warning('Failed to fetch Odoo products for integration %s', integration.id)

    # 3. Fetch Taxes (account.tax)
    tax_codes = []
    try:
        tax_domain = [['active', '=', True], ['type_tax_use', '=', 'sale']]
        if company_id:
            tax_domain.append(['company_id', '=', company_id])
        tax_ids = cast(
            list[int],
            _odoo_execute(
                models_proxy, creds.database_url, uid_int, creds.api_key,
                'account.tax', 'search',
                [tax_domain],
                {'limit': 500},
            ),
        )
        if tax_ids:
            tax_records = cast(
                list[dict[str, Any]],
                _odoo_execute(
                    models_proxy, creds.database_url, uid_int, creds.api_key,
                    'account.tax', 'read',
                    [tax_ids],
                    {'fields': ['id', 'name', 'description']},
                ),
            )
            for t in tax_records:
                name = str(t.get('name', ''))
                desc = str(t.get('description', '') or '')
                display = f'{name} - {desc}' if desc and desc != name else name
                tax_codes.append({
                    'id': str(t.get('id', '')),
                    'name': display,
                })
    except Exception:  # noqa: BLE001
        logger.warning('Failed to fetch Odoo taxes for integration %s', integration.id)

    return {
        'partners': sorted(partners, key=lambda x: x['name'].lower()),
        'products': sorted(products, key=lambda x: x['name'].lower()),
        'tax_codes': sorted(tax_codes, key=lambda x: x['name'].lower()),
    }



def test_integration_connection(integration: Integration):
    """Dispatch connection checks by integration type."""

    if integration.type == Integration.IntegrationType.SHOPIFY:
        return _test_shopify(integration)

    if integration.type == Integration.IntegrationType.ODOO:
        return _test_odoo(integration)

    if integration.type == Integration.IntegrationType.QUICKBOOKS:
        return _test_quickbooks(integration)

    return False, 'Unsupported integration type.'


# ---------------------------------------------------------------------------
# Odoo XML-RPC helpers
# ---------------------------------------------------------------------------

class _OdooTimeoutTransport(xmlrpc_client.SafeTransport):
    """XML-RPC transport with a configurable socket timeout."""

    def __init__(self, timeout: int = 30, **kwargs: Any):
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host):
        conn = super().make_connection(host)
        conn.timeout = self._timeout  # type: ignore[attr-defined]
        return conn


def _odoo_execute(
    models_proxy: xmlrpc_client.ServerProxy,
    db: str,
    uid: int,
    api_key: str,
    model: str,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any] | None = None,
    *,
    max_retries: int = 2,
) -> Any:
    """Execute an Odoo XML-RPC call with retry on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            return models_proxy.execute_kw(
                db, uid, api_key, model, method, args, kwargs or {},
            )
        except (xmlrpc_client.ProtocolError, OSError) as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                delay = min(1.0 * (2 ** attempt), 2.0)
                logger.warning(
                    'Odoo XML-RPC %s.%s failed (attempt %d/%d): %s — retrying in %.1fs',
                    model, method, attempt + 1, max_retries, exc, delay,
                )
                time.sleep(delay)
                continue
            break
    raise ValueError(f'Odoo API call {model}.{method} failed after {max_retries} attempts: {last_exc}')


def _get_odoo_connection(
    integration: Integration,
    order: Order,
) -> tuple[OdooCredentials, xmlrpc_client.ServerProxy, int, int]:
    """Authenticate to Odoo and return (creds, models_proxy, uid, partner_id).

    Centralises credential lookup, timeout, authentication, and partner
    resolution so callers don't duplicate this boilerplate.
    """
    creds = getattr(integration, 'odoo_credentials', None)
    if not creds:
        raise ValueError('Odoo credentials not configured for this integration.')

    base_url = creds.server_url.rstrip('/')
    transport = _OdooTimeoutTransport(timeout=30)
    common = xmlrpc_client.ServerProxy(f'{base_url}/xmlrpc/2/common', transport=transport)

    try:
        uid = cast(
            int | bool,
            common.authenticate(creds.database_url, creds.email, creds.api_key, {}),
        )
    except (xmlrpc_client.Fault, xmlrpc_client.ProtocolError, OSError) as exc:
        raise ValueError(f'Odoo authentication failed: {exc}') from exc

    if not uid:
        raise ValueError('Odoo authentication failed. Check credentials.')

    models_proxy = xmlrpc_client.ServerProxy(f'{base_url}/xmlrpc/2/object', transport=transport)
    partner_id = _resolve_configured_odoo_partner_id(creds, order)

    return creds, models_proxy, cast(int, uid), partner_id


def _resolve_odoo_product_by_sku(
    sku: str,
    models_proxy: xmlrpc_client.ServerProxy,
    creds: OdooCredentials,
    uid: int,
    sku_cache: dict[str, int | None] | None = None,
) -> int | None:
    """Resolve an Odoo product.product ID by matching the SKU to Odoo's
    Internal Reference (default_code).

    Uses an optional sku_cache dict to avoid redundant XML-RPC calls
    within the same order.
    """
    if not sku:
        return None

    if sku_cache is not None and sku in sku_cache:
        return sku_cache[sku]

    try:
        domain: list[list[Any] | str] = [['default_code', '=', sku]]
        if creds.company_id:
            try:
                comp_id = int(creds.company_id)
                domain.extend(['|', ['company_id', '=', False], ['company_id', '=', comp_id]])
            except (ValueError, TypeError):
                pass

        matches = cast(
            list[int],
            _odoo_execute(
                models_proxy, creds.database_url, uid, creds.api_key,
                'product.product', 'search',
                [domain],
                {'limit': 1},
            ),
        )
        result = matches[0] if matches else None
    except Exception:  # noqa: BLE001
        logger.warning('Odoo product lookup by SKU %r failed', sku)
        result = None

    if sku_cache is not None:
        sku_cache[sku] = result
    return result


def _apply_company_id(vals: dict[str, Any], creds: OdooCredentials) -> None:
    """Set company_id on an Odoo record dict, logging a warning on bad values."""
    if creds.company_id:
        try:
            vals['company_id'] = int(creds.company_id)
        except (ValueError, TypeError):
            logger.warning(
                'Odoo company_id %r is not numeric — ignoring. '
                'Update credentials with a valid integer.',
                creds.company_id,
            )


def _resolve_configured_odoo_partner_id(creds: OdooCredentials, order: Order) -> int:
    """Pick configured Odoo partner ID based on order source rules."""
    raw_tags = (order.shopify_tags or '').lower()
    tags = {tag.strip() for tag in raw_tags.split(',') if tag.strip()}

    if 'origin:sukhiba' in tags or 'origin:flowcart' in tags:
        selected_partner = (creds.sukhiba_partner_id or '').strip()
        source_label = 'Whatsapp'
    elif order.order_channel == Order.CHANNEL_POS:
        selected_partner = (creds.pos_partner_id or '').strip()
        source_label = 'POS channel'
    else:
        selected_partner = (creds.ecommerce_partner_id or '').strip()
        source_label = 'e-commerce default'

    if not selected_partner:
        raise ValueError(
            f'Missing configured Odoo partner ID for {source_label}. '
            'Update Odoo integration credentials with all partner IDs.'
        )

    try:
        return int(selected_partner)
    except ValueError as exc:
        raise ValueError(
            f'Configured Odoo partner ID for {source_label} must be numeric.'
        ) from exc


def create_odoo_sales_order(integration: Integration, order: Order) -> int:
    """
    Create a sale.order in Odoo for the given order via XML-RPC.
    Returns the Odoo record ID (integer).
    """
    creds, models_proxy, uid, partner_id = _get_odoo_connection(integration, order)

    sku_cache: dict[str, int | None] = {}
    order_lines: list[tuple[int, int, dict[str, Any]]] = []
    for item in order.items.all():
        line_vals: dict[str, Any] = {
            'name': item.product_name,
            'product_uom_qty': float(item.quantity),
            'price_unit': float(item.unit_price),
        }

        odoo_pid = _resolve_odoo_product_by_sku(item.sku, models_proxy, creds, uid, sku_cache)
        if odoo_pid:
            line_vals['product_id'] = odoo_pid
        elif creds.default_product_id:
            try:
                line_vals['product_id'] = int(creds.default_product_id)
            except (ValueError, TypeError):
                pass

        # Apply tax if configured
        if creds.tax_id:
            try:
                line_vals['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass

        order_lines.append((0, 0, line_vals))

    # Discount line item (negative price)
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line: dict[str, Any] = {
            'name': 'Discount',
            'product_uom_qty': 1,
            'price_unit': -discount_amount,
        }
        order_lines.append((0, 0, discount_line))

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'name': 'Shipping',
            'product_uom_qty': 1,
            'price_unit': shipping_price,
        }
        if creds.tax_id:
            try:
                shipping_line['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass
        order_lines.append((0, 0, shipping_line))

    so_vals: dict[str, Any] = {
        'partner_id': partner_id,
        'name': order.shopify_order_number,
        'client_order_ref': order.order_number,
        'order_line': order_lines,
    }
    _apply_company_id(so_vals, creds)

    odoo_so_id = cast(
        int,
        _odoo_execute(
            models_proxy, creds.database_url, uid, creds.api_key,
            'sale.order', 'create', [so_vals],
        ),
    )
    return odoo_so_id


def create_odoo_invoice_record(integration: Integration, order: Order) -> int:
    """
    Create an account.move (customer invoice) in Odoo for the given order via XML-RPC.
    Returns the Odoo record ID (integer).
    """
    creds, models_proxy, uid, partner_id = _get_odoo_connection(integration, order)

    sku_cache: dict[str, int | None] = {}
    invoice_lines: list[tuple[int, int, dict[str, Any]]] = []
    for item in order.items.all():
        line_vals: dict[str, Any] = {
            'name': item.product_name,
            'quantity': float(item.quantity),
            'price_unit': float(item.unit_price),
        }

        odoo_pid = _resolve_odoo_product_by_sku(item.sku, models_proxy, creds, uid, sku_cache)
        if odoo_pid:
            line_vals['product_id'] = odoo_pid
        elif creds.default_product_id:
            try:
                line_vals['product_id'] = int(creds.default_product_id)
            except (ValueError, TypeError):
                pass

        # Apply tax if configured
        if creds.tax_id:
            try:
                line_vals['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass

        invoice_lines.append((0, 0, line_vals))
    
    # Discount line item (negative price)
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line: dict[str, Any] = {
            'name': 'Discount',
            'quantity': 1,
            'price_unit': -discount_amount,
        }
        try:
            discount_line['product_id'] = int(creds.discount_product_id)
        except (ValueError, TypeError):
            pass
        invoice_lines.append((0, 0, discount_line))

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'name': 'Shipping',
            'quantity': 1,
            'price_unit': shipping_price,
        }
        try:
            shipping_line['account_id'] = int(creds.shipping_fee_account_id)
        except (ValueError, TypeError):
            pass
        if creds.tax_id:
            try:
                shipping_line['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass
        invoice_lines.append((0, 0, shipping_line))

    move_vals: dict[str, Any] = {
        'move_type': 'out_invoice',
        'partner_id': partner_id,
        'ref': order.order_number,
        'invoice_origin': order.shopify_order_number,
        'invoice_line_ids': invoice_lines,
    }
    _apply_company_id(move_vals, creds)

    odoo_invoice_id = cast(
        int,
        _odoo_execute(
            models_proxy, creds.database_url, uid, creds.api_key,
            'account.move', 'create', [move_vals],
        ),
    )

    # Auto confirm the Sales Invoice
    _odoo_execute(
        models_proxy, creds.database_url, uid, creds.api_key,
        'account.move', 'action_post', [[odoo_invoice_id]],
    )

    return odoo_invoice_id


def update_odoo_sales_order(integration: Integration, order: Order) -> None:
    """
    Update an existing sale.order in Odoo to reflect the current order state.
    Clears existing order lines and re-creates them from scratch.
    """
    if not order.odoo_sales_order_id:
        raise ValueError('No Odoo Sales Order ID on this order — nothing to update.')

    creds, models_proxy, uid, partner_id = _get_odoo_connection(integration, order)
    odoo_so_id = int(order.odoo_sales_order_id)

    sku_cache: dict[str, int | None] = {}
    order_lines: list[tuple[int, int, dict[str, Any]]] = []
    for item in order.items.all():
        line_vals: dict[str, Any] = {
            'name': item.product_name,
            'product_uom_qty': float(item.quantity),
            'price_unit': float(item.unit_price),
        }

        odoo_pid = _resolve_odoo_product_by_sku(item.sku, models_proxy, creds, uid, sku_cache)
        if odoo_pid:
            line_vals['product_id'] = odoo_pid
        elif creds.default_product_id:
            try:
                line_vals['product_id'] = int(creds.default_product_id)
            except (ValueError, TypeError):
                pass

        if creds.tax_id:
            try:
                line_vals['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass

        order_lines.append((0, 0, line_vals))
    
    # Discount line item (negative price)
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line: dict[str, Any] = {
            'name': 'Discount',
            'product_uom_qty': 1,
            'price_unit': -discount_amount,
        }
        order_lines.append((0, 0, discount_line))

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'name': 'Shipping',
            'product_uom_qty': 1,
            'price_unit': shipping_price,
        }
        if creds.tax_id:
            try:
                shipping_line['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass
        order_lines.append((0, 0, shipping_line))

    update_vals: dict[str, Any] = {
        'partner_id': partner_id,
        'name': order.shopify_order_number,
        'client_order_ref': order.order_number,
        # (5, 0, 0) = unlink all existing lines, then (0, 0, {...}) = create new
        'order_line': [(5, 0, 0)] + order_lines,
    }
    _apply_company_id(update_vals, creds)

    _odoo_execute(
        models_proxy, creds.database_url, uid, creds.api_key,
        'sale.order', 'write', [[odoo_so_id], update_vals],
    )


def update_odoo_invoice_record(integration: Integration, order: Order) -> None:
    """
    Update an existing account.move (customer invoice) in Odoo.
    Only works on invoices still in draft state.
    Clears existing lines and re-creates them from the current order.
    """
    if not order.odoo_sales_invoice_id:
        raise ValueError('No Odoo Invoice ID on this order — nothing to update.')

    creds, models_proxy, uid, partner_id = _get_odoo_connection(integration, order)
    odoo_inv_id = int(order.odoo_sales_invoice_id)

    sku_cache: dict[str, int | None] = {}
    invoice_lines: list[tuple[int, int, dict[str, Any]]] = []
    for item in order.items.all():
        line_vals: dict[str, Any] = {
            'name': item.product_name,
            'quantity': float(item.quantity),
            'price_unit': float(item.unit_price),
        }

        odoo_pid = _resolve_odoo_product_by_sku(item.sku, models_proxy, creds, uid, sku_cache)
        if odoo_pid:
            line_vals['product_id'] = odoo_pid
        elif creds.default_product_id:
            try:
                line_vals['product_id'] = int(creds.default_product_id)
            except (ValueError, TypeError):
                pass

        if creds.tax_id:
            try:
                line_vals['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass

        invoice_lines.append((0, 0, line_vals))
    
    # Discount line item (negative price)
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line: dict[str, Any] = {
            'name': 'Discount',
            'quantity': 1,
            'price_unit': -discount_amount,
        }
        try:
            discount_line['product_id'] = int(creds.discount_product_id)
        except (ValueError, TypeError):
            pass
        invoice_lines.append((0, 0, discount_line))

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'name': 'Shipping',
            'quantity': 1,
            'price_unit': shipping_price,
        }
        try:
            shipping_line['account_id'] = int(creds.shipping_fee_account_id)
        except (ValueError, TypeError):
            pass
        if creds.tax_id:
            try:
                shipping_line['tax_ids'] = [(6, 0, [int(creds.tax_id)])]
            except (ValueError, TypeError):
                pass
        invoice_lines.append((0, 0, shipping_line))

    update_vals: dict[str, Any] = {
        'partner_id': partner_id,
        'ref': order.order_number,
        'invoice_line_ids': [(5, 0, 0)] + invoice_lines,
    }
    _apply_company_id(update_vals, creds)

    _odoo_execute(
        models_proxy, creds.database_url, uid, creds.api_key,
        'account.move', 'write', [[odoo_inv_id], update_vals],
    )

def _resolve_configured_quickbooks_customer_id(
    creds: QuickBooksCredentials,
    order: Order,
) -> str:
    """Pick configured QuickBooks customer ID based on order source rules."""
    raw_tags = (order.shopify_tags or '').lower()
    tags = {tag.strip() for tag in raw_tags.split(',') if tag.strip()}

    if 'origin:sukhiba' in tags or 'origin:flowcart' in tags:
        selected_customer = (creds.sukhiba_customer_id or '').strip()
        source_label = 'Whatsapp'
    elif order.order_channel == Order.CHANNEL_POS:
        selected_customer = (creds.pos_customer_id or '').strip()
        source_label = 'POS channel'
    else:
        selected_customer = (creds.ecommerce_customer_id or '').strip()
        source_label = 'e-commerce default'

    if not selected_customer:
        raise ValueError(
            f'Missing configured QuickBooks customer ID for {source_label}. '
            'Update QuickBooks integration credentials with all customer IDs.'
        )

    return selected_customer


def _fetch_qb_sku_map(creds: 'QuickBooksCredentials') -> dict[str, str]:
    """Return a lowercase-key → QB-item-id mapping for all active QB items.

    Each item contributes up to two entries:
    - Its ``Name`` (display name) as a fallback key.
    - Its ``Sku`` field as the preferred key (overrides the Name entry when
      both are present, so order-line SKUs resolve against the dedicated QBO
      SKU field first).

    This lets the SKU on an order line match the ``Sku`` attribute stored on
    the QBO item rather than requiring the item's display name to equal the SKU.
    """
    from urllib.parse import quote as _quote
    query = _quote('SELECT Id, Name, Sku FROM Item WHERE Active = true MAXRESULTS 1000')
    url = (
        f'{creds.api_base_url}/v3/company/{creds.realm_id}'
        f'/query?query={query}&minorversion=75'
    )
    resp = _quickbooks_request('GET', url, creds)
    if resp.status_code != 200:
        logger.warning('QB item map fetch returned %d — SKU lookup disabled', resp.status_code)
        return {}
    items = resp.json().get('QueryResponse', {}).get('Item', [])
    sku_map: dict[str, str] = {}
    for i in items:
        item_id = str(i.get('Id', ''))
        if not item_id:
            continue
        name = str(i.get('Name', '')).strip().lower()
        sku = str(i.get('Sku', '')).strip().lower()
        if name:
            sku_map[name] = item_id   # display-name fallback
        if sku:
            sku_map[sku] = item_id    # Sku field takes priority
    return sku_map


def _resolve_qb_item_ref(
    item_sku: str,
    item_name: str,
    sku_map: dict[str, str],
    default_product_id: str,
    mapped_sku: str | None = None,
) -> dict[str, str] | None:
    """Return the QB ItemRef dict for a line item.

    Lookup order:
    1. If `mapped_sku` is provided, use it to match against the QB items map.
    2. Case-insensitive match of the order SKU against the QB items map
       (which prefers the QBO item's ``Sku`` field over its display name)
       → {'value': id}
    3. Case-insensitive match of the order product name against the QB items map
       → {'value': id}
    4. Configured default product → {'value': default_product_id}
    5. No match and no default   → None (QB will omit ItemRef)
    """
    if mapped_sku:
        # Try mapped_sku as Item ID
        if mapped_sku in sku_map.values():
            return {'value': mapped_sku}
        # Try mapped_sku as SKU
        mapped_sku_id = sku_map.get(mapped_sku.lower())
        if mapped_sku_id:
            return {'value': mapped_sku_id}
        logger.warning('QB mapped external_sku "%s" not found as Item ID or SKU in active items', mapped_sku)

    if item_sku:
        item_id = sku_map.get(item_sku.lower())
        if item_id:
            return {'value': item_id}
        logger.warning('QB SKU "%s" not found in items list  falling back to name/default', item_sku)

    if item_name:
        item_id = sku_map.get(item_name.lower())
        if item_id:
            return {'value': item_id}

    if default_product_id:
        return {'value': default_product_id}
    return None


def create_quickbooks_sales_invoice(integration: Integration, order: Order) -> str:
    """Create a sales invoice in QuickBooks and return the QuickBooks invoice ID."""
    creds = getattr(integration, 'quickbooks_credentials', None)
    if not creds:
        raise ValueError('QuickBooks credentials not configured for this integration.')

    _ensure_qb_environment(creds)

    customer_id = _resolve_configured_quickbooks_customer_id(creds, order)

    # Fetch QB items once up-front so we can resolve SKUs to stable item IDs.
    sku_map = _fetch_qb_sku_map(creds)

    endpoint = f'{creds.api_base_url}/v3/company/{creds.realm_id}/invoice?minorversion=75'

    lines: list[dict[str, Any]] = []
    for item in order.items.select_related('product').all():
        amount = float(item.total_price)

        # Try to find a specific mapping for this product/integration
        mapped_sku = None
        if item.product_id:
            from .models import ProductIntegrationMapping
            mapping = ProductIntegrationMapping.objects.filter(
                product_id=item.product_id,
                integration_id=integration.id
            ).first()
            if mapping:
                mapped_sku = mapping.external_sku

        item_ref = _resolve_qb_item_ref(
            (item.sku or '').strip(),
            (item.product_name or '').strip(),
            sku_map,
            str(creds.default_product_id or ''),
            mapped_sku=mapped_sku,
        )

        is_bundle = bool(item.product_id and item.product and item.product.is_bundle)

        if is_bundle and item_ref:
            # QB Group items must use GroupLineDetail — Amount and TaxCodeRef are
            # computed by QB from the group definition and must not be sent.
            line = {
                'DetailType': 'GroupLineDetail',
                'GroupLineDetail': {
                    'GroupItemRef': item_ref,
                    'Quantity': float(item.quantity),
                },
            }
        else:
            line = {
                'DetailType': 'SalesItemLineDetail',
                'Amount': amount,
                'Description': item.product_name,
                'SalesItemLineDetail': {
                    'Qty': float(item.quantity),
                    'UnitPrice': float(item.unit_price),
                },
            }
            if item_ref:
                line['SalesItemLineDetail']['ItemRef'] = item_ref
            if creds.tax_id:
                line['SalesItemLineDetail']['TaxCodeRef'] = {'value': creds.tax_id}

        lines.append(line)

    # Discount line item (negative amount) if order-level discount exists
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line = {
            'DetailType': 'SalesItemLineDetail',
            'Amount': -discount_amount,
            'Description': 'Order-level discount',
            'SalesItemLineDetail': {
                'Qty': 1,
                'UnitPrice': -discount_amount,
            },
        }
        lines.append(discount_line)

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'DetailType': 'SalesItemLineDetail',
            'Amount': shipping_price,
            'Description': 'Shipping',
            'SalesItemLineDetail': {
                'ItemRef': {'value': creds.shipping_fee_account_id},
                'Qty': 1,
                'UnitPrice': shipping_price,
            },
        }
        if creds.tax_id:
            shipping_line['SalesItemLineDetail']['TaxCodeRef'] = {'value': creds.tax_id}
        lines.append(shipping_line)

    # Invoice number: prefix + Shopify order number with leading '#' stripped.
    # e.g. prefix="SHT", shopify_order_number="#1515" → "SHT1515"
    prefix = str(creds.invoice_prefix or '').strip()
    raw_number = str(order.shopify_order_number).lstrip('#')
    doc_number = f'{prefix}{raw_number}'[:21]

    payload: dict[str, Any] = {
        'DocNumber': doc_number,
        'CustomerRef': {'value': customer_id},
        'PrivateNote': f'Omni-Link order {order.shopify_order_number}',
        'Line': lines,
    }

    response = _quickbooks_request('POST', endpoint, creds, json_payload=payload)

    if response.status_code >= 400:
        detail = response.text
        try:
            parsed = response.json()
            fault = parsed.get('Fault', {}) if isinstance(parsed, dict) else {}
            errors = fault.get('Error', []) if isinstance(fault, dict) else []
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    detail = str(first.get('Detail') or first.get('Message') or detail)
        except ValueError:
            pass
        raise ValueError(f'QuickBooks invoice creation failed: {detail}')

    body = cast(dict[str, Any], response.json())
    invoice = cast(dict[str, Any], body.get('Invoice') or {})
    invoice_id = str(invoice.get('Id') or '').strip()
    if not invoice_id:
        raise ValueError('QuickBooks invoice creation succeeded but no Invoice ID was returned.')

    return invoice_id


def update_quickbooks_invoice(integration: Integration, order: Order) -> None:
    """
    Update an existing QuickBooks invoice to reflect the current order state.
    Reads the existing invoice to obtain its SyncToken, rebuilds line items,
    and POSTs the update.
    """
    if not order.quickbooks_sales_invoice_id:
        raise ValueError('No QuickBooks Invoice ID on this order — nothing to update.')

    creds = getattr(integration, 'quickbooks_credentials', None)
    if not creds:
        raise ValueError('QuickBooks credentials not configured for this integration.')

    _ensure_qb_environment(creds)

    qb_invoice_id = order.quickbooks_sales_invoice_id
    customer_id = _resolve_configured_quickbooks_customer_id(creds, order)

    # 1. Read existing invoice to get SyncToken (required for optimistic locking)
    read_url = (
        f'{creds.api_base_url}/v3/company/{creds.realm_id}'
        f'/invoice/{qb_invoice_id}?minorversion=75'
    )
    read_resp = _quickbooks_request('GET', read_url, creds)
    if read_resp.status_code >= 400:
        raise ValueError(
            f'Failed to read existing QuickBooks Invoice {qb_invoice_id}: '
            f'status {read_resp.status_code}'
        )
    existing = cast(dict[str, Any], read_resp.json())
    existing_invoice = cast(dict[str, Any], existing.get('Invoice') or {})
    sync_token = str(existing_invoice.get('SyncToken') or '0')

    # 2. Build updated line items
    sku_map = _fetch_qb_sku_map(creds)

    lines: list[dict[str, Any]] = []
    for item in order.items.select_related('product').all():
        amount = float(item.total_price)

        # Try to find a specific mapping for this product/integration
        mapped_sku = None
        if item.product_id:
            from .models import ProductIntegrationMapping
            mapping = ProductIntegrationMapping.objects.filter(
                product_id=item.product_id,
                integration_id=integration.id
            ).first()
            if mapping:
                mapped_sku = mapping.external_sku

        item_ref = _resolve_qb_item_ref(
            (item.sku or '').strip(),
            (item.product_name or '').strip(),
            sku_map,
            str(creds.default_product_id or ''),
            mapped_sku=mapped_sku,
        )

        is_bundle = bool(item.product_id and item.product and item.product.is_bundle)

        if is_bundle and item_ref:
            line: dict[str, Any] = {
                'DetailType': 'GroupLineDetail',
                'GroupLineDetail': {
                    'GroupItemRef': item_ref,
                    'Quantity': float(item.quantity),
                },
            }
        else:
            line = {
                'DetailType': 'SalesItemLineDetail',
                'Amount': amount,
                'Description': item.product_name,
                'SalesItemLineDetail': {
                    'Qty': float(item.quantity),
                    'UnitPrice': float(item.unit_price),
                },
            }
            if item_ref:
                line['SalesItemLineDetail']['ItemRef'] = item_ref
            if creds.tax_id:
                line['SalesItemLineDetail']['TaxCodeRef'] = {'value': creds.tax_id}

        lines.append(line)

    # Discount line item (negative amount) if order-level discount exists
    discount_amount = float(order.discount_amount or 0)
    if discount_amount > 0:
        discount_line = {
            'DetailType': 'SalesItemLineDetail',
            'Amount': -discount_amount,
            'Description': 'Order Discount',
            'SalesItemLineDetail': {
                'Qty': 1,
                'UnitPrice': -discount_amount,
            },
        }
        lines.append(discount_line)

    # Shipping fee line item
    shipping_price = float(order.shipping_price or 0)
    if shipping_price > 0 and creds.shipping_fee_account_id:
        shipping_line: dict[str, Any] = {
            'DetailType': 'SalesItemLineDetail',
            'Amount': shipping_price,
            'Description': 'Shipping',
            'SalesItemLineDetail': {
                'ItemRef': {'value': creds.shipping_fee_account_id},
                'Qty': 1,
                'UnitPrice': shipping_price,
            },
        }
        if creds.tax_id:
            shipping_line['SalesItemLineDetail']['TaxCodeRef'] = {'value': creds.tax_id}
        lines.append(shipping_line)

    prefix = str(creds.invoice_prefix or '').strip()
    raw_number = str(order.shopify_order_number).lstrip('#')
    doc_number = f'{prefix}{raw_number}'[:21]

    # 3. POST update — must include Id and SyncToken
    payload: dict[str, Any] = {
        'Id': qb_invoice_id,
        'SyncToken': sync_token,
        'DocNumber': doc_number,
        'CustomerRef': {'value': customer_id},
        'PrivateNote': f'Omni-Link order {order.order_number} (updated)',
        'Line': lines,
    }

    update_url = (
        f'{creds.api_base_url}/v3/company/{creds.realm_id}'
        f'/invoice?minorversion=75'
    )
    response = _quickbooks_request('POST', update_url, creds, json_payload=payload)

    if response.status_code >= 400:
        detail = response.text
        try:
            parsed = response.json()
            fault = parsed.get('Fault', {}) if isinstance(parsed, dict) else {}
            errors = fault.get('Error', []) if isinstance(fault, dict) else []
            if isinstance(errors, list) and errors:
                first = errors[0]
                if isinstance(first, dict):
                    detail = str(first.get('Detail') or first.get('Message') or detail)
        except ValueError:
            pass
        raise ValueError(f'QuickBooks invoice update failed: {detail}')


def _normalize_market_and_currency(
    integration: Integration,
    shopify_order: dict[str, Any],
) -> tuple[str, str]:
    """Apply market/currency business rules for Shopify imports."""
    integration_market = (integration.market or "").strip().lower()

    # Rule 1: Kenyan Shopify integration always maps to Kenyan market + KES.
    if integration.type == Integration.IntegrationType.SHOPIFY and integration_market in {
        "kenya",
        "kenyan",
        "ke",
    }:
        return "Kenya", "KES"

    currency = (
        shopify_order.get("currency")
        or shopify_order.get("presentment_currency")
        or "USD"
    )
    return integration.market, currency


def _extract_tags(order_payload: dict[str, Any]) -> list[str]:
    tags = order_payload.get("tags", "")
    if isinstance(tags, list):
        return [str(tag).strip().lower() for tag in tags if str(tag).strip()]
    if isinstance(tags, str):
        return [tag.strip().lower() for tag in tags.split(",") if tag.strip()]
    return []


def _is_pos_order(order_payload: dict[str, Any]) -> bool:
    """Best-effort POS detection from Shopify order payloads."""
    source_name = str(order_payload.get("source_name", "")).lower()
    source_identifier = str(order_payload.get("source_identifier", "")).lower()

    if source_name in {"pos", "shopify_pos"}:
        return True
    if "pos" in source_identifier:
        return True

    # Some payloads include app/channel metadata.
    channel_information = order_payload.get("channel_information") or {}
    app = channel_information.get("app") if isinstance(channel_information, dict) else None
    app_title = ""
    if isinstance(app, dict):
        app_title = str(app.get("title", "")).lower()
    if "point of sale" in app_title or "shopify pos" in app_title:
        return True

    return False


def _resolve_order_channel(order_payload: dict[str, Any]) -> str:
    """Rule set for order channel mapping.

    Priority:
    1) POS order -> POS
    2) tags contain origin:sukhiba or origin:flowcart -> WHATSAPP
    3) default -> WEBSITE
    """
    if _is_pos_order(order_payload):
        return Order.CHANNEL_POS

    tags = _extract_tags(order_payload)
    if "origin:sukhiba" in tags or "origin:flowcart" in tags:
        return Order.CHANNEL_WHATSAPP

    return Order.CHANNEL_WEBSITE


def _resolve_payment_method(order_payload: dict[str, Any]) -> tuple[str, bool, str]:
    gateway_names = order_payload.get("payment_gateway_names") or []
    gateways = [str(name).lower() for name in gateway_names]

    is_cod = any(
        marker in " ".join(gateways)
        for marker in ["cash on delivery", "cod", "cash_on_delivery"]
    )
    if is_cod:
        return Order.CASH_ON_DELIVERY, True, ",".join(gateway_names)

    return Order.PREPAID, False, ",".join(gateway_names)


def _map_payment_status(financial_status: str) -> str:
    mapping = {
        "paid": Order.PAID,
        "authorized": Order.AUTHORIZED,
        "partially_paid": Order.PARTIALLY_PAID,
        "partially_refunded": Order.PARTIALLY_REFUNDED,
        "refunded": Order.REFUNDED_PAYMENT,
        "voided": Order.VOIDED,
        "pending": Order.PENDING_PAYMENT,
    }
    return mapping.get((financial_status or "").lower(), Order.PENDING_PAYMENT)


def _map_fulfillment_status(fulfillment_status: str) -> str:
    mapping = {
        "fulfilled": Order.FULFILLED,
        "partial": Order.PARTIALLY_FULFILLED,
        "partially_fulfilled": Order.PARTIALLY_FULFILLED,
        "restocked": Order.RESTOCKED,
        "unfulfilled": Order.UNFULFILLED,
    }
    return mapping.get((fulfillment_status or "").lower(), Order.UNFULFILLED)


def _as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _resolve_market_for_order(market_name: str, currency: str) -> Market:
    normalized_name = (market_name or 'Unknown').strip() or 'Unknown'
    normalized_currency = (currency or 'USD').strip().upper() or 'USD'

    market = Market.objects.filter(name__iexact=normalized_name).first()
    if market:
        return market

    market = Market.objects.filter(code__iexact=normalized_name).first()
    if market:
        return market

    code = ''.join(ch for ch in normalized_name.upper() if ch.isalpha())[:3] or 'MKT'
    candidate = code
    i = 1
    while Market.objects.filter(code=candidate).exists():
        suffix = str(i)
        candidate = f"{code[: max(1, 3 - len(suffix))]}{suffix}"
        i += 1

    market, _ = Market.objects.get_or_create(
        name=normalized_name,
        defaults={
            'code': candidate,
            'currency': normalized_currency,
            'is_active': True,
        },
    )
    return market


def _fetch_shopify_orders(
    integration: Integration,
    created_at_min: datetime,
    created_at_max: datetime,
) -> list[dict[str, Any]]:
    creds = getattr(integration, "shopify_credentials", None)
    if not creds:
        raise ValueError("Shopify credentials not configured.")

    endpoint = f"{creds.store_url.rstrip('/')}/admin/api/{creds.api_version}/orders.json"
    headers = {
        "X-Shopify-Access-Token": creds.access_token,
        "Content-Type": "application/json",
    }

    params = {
        "status": "any",
        "limit": 250,
        "created_at_min": created_at_min.isoformat(),
        "created_at_max": created_at_max.isoformat(),
    }

    orders: list[dict[str, Any]] = []
    response = requests.get(endpoint, headers=headers, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    orders.extend(payload.get("orders", []))

    # Basic cursor pagination support.
    while "rel=\"next\"" in response.headers.get("Link", ""):
        link_header = response.headers.get("Link", "")
        next_url = None
        for part in link_header.split(","):
            if "rel=\"next\"" in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break

        response = requests.get(next_url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        orders.extend(payload.get("orders", []))

    return orders


def _normalize_shop_domain(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        host = urlparse(raw).netloc
    else:
        host = raw.split("/", 1)[0]
    return host.split(":", 1)[0]


def _resolve_shopify_integration(shop_domain: str) -> Integration | None:
    if not shop_domain:
        return None

    integrations = Integration.objects.filter(
        type=Integration.IntegrationType.SHOPIFY
    ).select_related("warehouse__manager", "shopify_credentials")

    normalized_shop = _normalize_shop_domain(shop_domain)
    for integration in integrations:
        creds = getattr(integration, "shopify_credentials", None)
        if not creds:
            continue
        if _normalize_shop_domain(creds.store_url) == normalized_shop:
            return integration
    return None


def resolve_shopify_integration_by_shop_domain(shop_domain: str) -> Integration | None:
    """Public helper for webhook views to resolve integration by shop domain."""
    return _resolve_shopify_integration(shop_domain)


def verify_shopify_webhook_hmac(raw_body: bytes, hmac_header: str, secret: str) -> bool:
    """Verify a Shopify webhook HMAC-SHA256 signature.

    ``secret`` must be the Shopify *Webhooks signing secret*, which is found in:
    - **Admin-created webhooks**: Shopify Admin → Settings → Notifications →
      Webhooks → "Your webhooks will be signed with …"
    - **App webhooks (Partners / Custom apps)**: Partner Dashboard → App →
      Client credentials → Client secret  (same value as ``api_secret`` /
      API Secret Key).

    These two secrets are DIFFERENT values for the same store; make sure you
    use the one that matches how the webhook subscription was registered.
    """
    if not raw_body:
        logger.warning('verify_shopify_webhook_hmac: raw_body is empty — cannot verify.')
        return False
    if not hmac_header:
        logger.warning('verify_shopify_webhook_hmac: X-Shopify-Hmac-Sha256 header is missing.')
        return False
    if not secret:
        logger.warning('verify_shopify_webhook_hmac: api_secret is blank — cannot verify.')
        return False

    digest = hmac.new(
        secret.encode('utf-8'),
        raw_body,
        hashlib.sha256,
    ).digest()
    computed_hmac = base64.b64encode(digest).decode('utf-8')

    match = hmac.compare_digest(computed_hmac, hmac_header)
    if not match:
        logger.warning(
            'verify_shopify_webhook_hmac: mismatch '
            '(body_len=%d, computed[0:8]=%r, received[0:8]=%r). '
            'Check that api_secret is the correct Shopify Webhooks signing secret.',
            len(raw_body),
            computed_hmac[:8],
            hmac_header[:8],
        )
    return match


def _resolve_owner_for_integration(integration: Integration) -> Any:
    # Webhooks are system-to-system calls. Use warehouse manager if present.
    if integration.warehouse_id and integration.warehouse and integration.warehouse.manager:
        return integration.warehouse.manager

    User = get_user_model()
    return (
        User.objects.filter(is_active=True)
        .order_by("id")
        .first()
    )


def _extract_order_line_image_url(line: dict[str, Any], product: Product | None) -> str | None:
    line_image_url = None
    if isinstance(line.get("image"), dict):
        line_image_url = str(line.get("image", {}).get("src") or "").strip() or None
    if not line_image_url and product and product.image_url:
        line_image_url = product.image_url.strip() or None
    return line_image_url


def _upsert_shopify_order_from_payload(
    integration: Integration,
    owner,
    raw: dict[str, Any],
) -> tuple[Order | None, bool, bool]:
    """Create or update one Shopify order payload.

    Returns: (order, created, skipped)
    """
    shopify_order_id = str(raw.get("id") or "").strip()
    if not shopify_order_id:
        return None, False, True

    market, currency = _normalize_market_and_currency(integration, raw)
    market_obj = _resolve_market_for_order(market, currency)
    payment_method, is_cod, gateway = _resolve_payment_method(raw)

    defaults = {
        "shopify_order_number": str(raw.get("order_number") or raw.get("name") or ""),
        "market": market_obj,
        "customer_email": raw.get("email") or "",
        "customer_name": (
            f"{(raw.get('customer') or {}).get('first_name', '')} "
            f"{(raw.get('customer') or {}).get('last_name', '')}"
        ).strip() or "Unknown Customer",
        "customer_phone": raw.get("phone") or "",
        "shopify_customer_id": str((raw.get("customer") or {}).get("id") or ""),
        "status": Order.CONFIRMED,
        "fulfillment_status": _map_fulfillment_status(raw.get("fulfillment_status", "")),
        "payment_status": _map_payment_status(raw.get("financial_status", "")),
        "delivery_status": Order.PENDING_DELIVERY,
        "payment_method": payment_method,
        "order_channel": _resolve_order_channel(raw),
        "is_cash_on_delivery": is_cod,
        "payment_gateway": gateway,
        "subtotal_price": _as_decimal(raw.get("subtotal_price")),
        "total_tax": _as_decimal(raw.get("total_tax")),
        "shipping_price": _as_decimal(raw.get("total_shipping_price_set", {}).get("shop_money", {}).get("amount") or raw.get("shipping_lines", [{}])[0].get("price") if raw.get("shipping_lines") else 0),
        "discount_amount": _as_decimal(raw.get("total_discounts")),
        "total_amount": _as_decimal(raw.get("total_price")),
        "shipping_address_line1": (raw.get("shipping_address") or {}).get("address1") or "",
        "shipping_address_line2": (raw.get("shipping_address") or {}).get("address2") or "",
        "shipping_city": (raw.get("shipping_address") or {}).get("city") or "",
        "shipping_state": (raw.get("shipping_address") or {}).get("province") or "",
        "shipping_postal_code": (raw.get("shipping_address") or {}).get("zip") or "",
        "shipping_country": (raw.get("shipping_address") or {}).get("country") or market,
        "shipping_country_code": (raw.get("shipping_address") or {}).get("country_code") or "",
        "billing_address_line1": (raw.get("billing_address") or {}).get("address1") or "",
        "billing_address_line2": (raw.get("billing_address") or {}).get("address2") or "",
        "billing_city": (raw.get("billing_address") or {}).get("city") or "",
        "billing_state": (raw.get("billing_address") or {}).get("province") or "",
        "billing_postal_code": (raw.get("billing_address") or {}).get("zip") or "",
        "billing_country": (raw.get("billing_address") or {}).get("country") or "",
        "shipping_method": (raw.get("shipping_lines") or [{}])[0].get("title") or "",
        "tracking_number": "",
        "shopify_tags": raw.get("tags") or "",
        "shopify_note": raw.get("note") or "",
        "customer_note": raw.get("note") or "",
        "discount_codes": ",".join(code.get("code", "") for code in raw.get("discount_codes", [])),
        "warehouse": integration.warehouse,
        "owner": owner,
        "shopify_created_at": _parse_datetime(raw.get("created_at")),
        "shopify_updated_at": _parse_datetime(raw.get("updated_at")),
        "shopify_raw_data": raw,
    }

    order, created = Order.objects.update_or_create(
        shopify_order_id=shopify_order_id,
        defaults={
            **defaults,
            "order_number": f"SHOP-{shopify_order_id}",
        },
    )

    order.items.all().delete()
    for line in raw.get("line_items", []):
        sku = str(line.get("sku") or "").strip()
        product = Product.objects.filter(sku=sku).first() if sku else None
        quantity = int(line.get("quantity") or 1)
        unit_price = _as_decimal(line.get("price"))

        OrderItem.objects.create(
            order=order,
            product=product,
            shopify_product_id=str(line.get("product_id") or ""),
            shopify_variant_id=str(line.get("variant_id") or ""),
            product_name=line.get("title") or sku or "Unnamed Product",
            variant_name=line.get("variant_title") or "",
            sku=sku,
            product_image_url=_extract_order_line_image_url(line, product),
            quantity=quantity,
            unit_price=unit_price,
            total_price=unit_price * quantity,
            tax_amount=_as_decimal(line.get("total_tax")),
            tax_rate=defaults["tax_rate"] if "tax_rate" in defaults else Decimal("0"),
            discount_amount=_as_decimal(line.get("total_discount")),
            fulfillment_status=line.get("fulfillment_status") or "UNFULFILLED",
            requires_shipping=bool(line.get("requires_shipping", True)),
            is_gift_card=bool(line.get("gift_card", False)),
            weight=_as_decimal(line.get("grams")),
            weight_unit="g",
            vendor=line.get("vendor") or "",
            properties={"shopify_line_item_id": line.get("id")},
        )

    # Auto-sync to ERP platforms if enabled
    if created:
        try:
            auto_sync_order_to_erp(order)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-sync failed for order %s", order.order_number)
    else:
        try:
            auto_update_order_in_erp(order)
        except Exception:  # noqa: BLE001
            logger.exception("Auto-update ERP failed for order %s", order.order_number)

    return order, created, False


def _upsert_shopify_product_from_payload(
    integration: Integration,
    payload: dict[str, Any],
) -> tuple[int, int, int, int]:
    """Upsert one Shopify product payload; returns counters.

    Returns: (imported, updated, skipped, bundles)
    """
    imported = 0
    updated = 0
    skipped = 0
    bundles = 0

    shopify_product_id = str(payload.get("id") or "").strip()
    variants = payload.get("variants", []) or []
    if not variants:
        return imported, updated, skipped + 1, bundles

    metafields = _fetch_shopify_product_metafields(integration, shopify_product_id)
    is_bundle = _is_bundle_product(payload, metafields)

    for variant in variants:
        sku = str(variant.get("sku") or "").strip()
        if not sku:
            skipped += 1
            continue

        variant_title = str(variant.get("title") or "").strip()
        base_name = str(payload.get("title") or sku).strip()
        name = base_name if variant_title.lower() in {"", "default title"} else f"{base_name} - {variant_title}"

        category = _get_or_create_category_by_name(payload.get("product_type"))
        image_url = ""
        images = payload.get("images", []) or []
        if images and isinstance(images[0], dict):
            image_url = str(images[0].get("src") or "")

        product, created = Product.objects.update_or_create(
            sku=sku,
            defaults={
                "name": name,
                "description": str(payload.get("body_html") or ""),
                "category": category,
                "price": _as_decimal(variant.get("price")),
                "reorder_level": 10,
                "image_url": image_url,
                "is_bundle": is_bundle,
            },
        )
        if created:
            imported += 1
        else:
            updated += 1

        if is_bundle:
            bundles += 1

        if is_bundle and variant == variants[0]:
            ProductBundle.objects.filter(bundle=product).delete()

    return imported, updated, skipped, bundles


def _claim_shopify_webhook_delivery(
    webhook_id: str,
    topic: str,
    shop_domain: str,
) -> tuple[ShopifyWebhookDelivery, bool]:
    """Create-or-lock webhook delivery row for idempotency control."""
    with transaction.atomic():
        delivery, created = ShopifyWebhookDelivery.objects.select_for_update().get_or_create(
            webhook_id=webhook_id,
            defaults={
                "topic": topic,
                "shop_domain": _normalize_shop_domain(shop_domain),
                "status": ShopifyWebhookDelivery.Status.RECEIVED,
            },
        )

        if not created and delivery.status == ShopifyWebhookDelivery.Status.FAILED:
            # Allow retries for previously failed deliveries.
            delivery.topic = topic
            delivery.shop_domain = _normalize_shop_domain(shop_domain)
            delivery.status = ShopifyWebhookDelivery.Status.RECEIVED
            delivery.error_message = ""
            delivery.processed_at = None
            delivery.save(
                update_fields=[
                    "topic",
                    "shop_domain",
                    "status",
                    "error_message",
                    "processed_at",
                    "updated_at",
                ]
            )

    return delivery, created


def _mark_webhook_delivery_processed(delivery: ShopifyWebhookDelivery):
    delivery.status = ShopifyWebhookDelivery.Status.PROCESSED
    delivery.error_message = ""
    delivery.processed_at = timezone.now()
    delivery.save(update_fields=["status", "error_message", "processed_at", "updated_at"])


def _mark_webhook_delivery_failed(delivery: ShopifyWebhookDelivery, error_message: str):
    delivery.status = ShopifyWebhookDelivery.Status.FAILED
    delivery.error_message = str(error_message)[:5000]
    delivery.save(update_fields=["status", "error_message", "updated_at"])


def process_shopify_webhook_event(
    topic: str,
    shop_domain: str,
    payload: dict[str, Any],
    webhook_id: str | None = None,
) -> dict[str, Any]:
    """Process one Shopify webhook payload and return stats/result."""
    normalized_topic = (topic or "").strip().lower()
    normalized_shop_domain = _normalize_shop_domain(shop_domain)

    delivery = None
    if webhook_id:
        delivery, created = _claim_shopify_webhook_delivery(
            webhook_id=webhook_id,
            topic=normalized_topic,
            shop_domain=normalized_shop_domain,
        )
        if not created and delivery.status == ShopifyWebhookDelivery.Status.PROCESSED:
            return {
                "topic": normalized_topic,
                "webhook_id": webhook_id,
                "duplicate": True,
                "message": "Duplicate delivery ignored.",
            }

    integration = _resolve_shopify_integration(normalized_shop_domain)
    if not integration:
        if delivery:
            _mark_webhook_delivery_failed(delivery, "No Shopify integration found for this shop domain.")
        raise ValueError("No Shopify integration found for this shop domain.")

    try:
        if normalized_topic in {"orders/create", "orders/updated"}:
            owner = _resolve_owner_for_integration(integration)
            if owner is None:
                raise ValueError("No active user available to own imported Shopify orders.")

            _, created, skipped = _upsert_shopify_order_from_payload(
                integration=integration,
                owner=owner,
                raw=payload,
            )
            integration.last_sync = timezone.now()
            integration.status = Integration.IntegrationStatus.ACTIVE
            integration.save(update_fields=["last_sync", "status", "updated_at"])

            if delivery:
                _mark_webhook_delivery_processed(delivery)

            return {
                "topic": normalized_topic,
                "integration_id": integration.id,
                "webhook_id": webhook_id,
                "created": 1 if created else 0,
                "updated": 0 if created else (0 if skipped else 1),
                "skipped": 1 if skipped else 0,
            }

        if normalized_topic in {"products/create", "products/update", "products/updated"}:
            imported, updated, skipped, bundles = _upsert_shopify_product_from_payload(
                integration=integration,
                payload=payload,
            )
            integration.last_sync = timezone.now()
            integration.status = Integration.IntegrationStatus.ACTIVE
            integration.save(update_fields=["last_sync", "status", "updated_at"])

            if delivery:
                _mark_webhook_delivery_processed(delivery)

            return {
                "topic": normalized_topic,
                "integration_id": integration.id,
                "webhook_id": webhook_id,
                "imported": imported,
                "updated": updated,
                "skipped": skipped,
                "bundles": bundles,
            }

        raise ValueError(f"Unsupported Shopify webhook topic: {topic}")
    except Exception as exc:  # noqa: BLE001
        if delivery:
            _mark_webhook_delivery_failed(delivery, str(exc))
        raise


@transaction.atomic
def import_shopify_orders(
    integration: Integration,
    owner: Any,
    created_at_min: datetime,
    created_at_max: datetime,
) -> dict[str, int]:
    """Import Shopify orders and line items with deterministic channel mapping."""
    if integration.type != Integration.IntegrationType.SHOPIFY:
        raise ValueError("Only Shopify integrations support this import.")

    imported = 0
    updated = 0
    skipped = 0

    shopify_orders = _fetch_shopify_orders(
        integration,
        created_at_min=created_at_min,
        created_at_max=created_at_max,
    )

    for raw in shopify_orders:
        _, created, was_skipped = _upsert_shopify_order_from_payload(
            integration=integration,
            owner=owner,
            raw=raw,
        )

        if was_skipped:
            skipped += 1
            continue
        if created:
            imported += 1
        else:
            updated += 1

    integration.last_sync = timezone.now()
    integration.status = Integration.IntegrationStatus.ACTIVE
    integration.save(update_fields=["last_sync", "status", "updated_at"])

    return {
        "total": len(shopify_orders),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
    }


def _fetch_shopify_products(integration: Integration) -> list[dict[str, Any]]:
    """Fetch all active products from Shopify using REST API with pagination."""
    creds = getattr(integration, "shopify_credentials", None)
    if not creds:
        raise ValueError("Shopify credentials not configured.")

    endpoint = f"{creds.store_url.rstrip('/')}/admin/api/{creds.api_version}/products.json"
    headers = {
        "X-Shopify-Access-Token": creds.access_token,
        "Content-Type": "application/json",
    }

    params = {
        "status": "active",
        "limit": 250,
        "fields": "id,title,body_html,product_type,tags,variants,images",
    }

    products: list[dict[str, Any]] = []
    response = requests.get(endpoint, headers=headers, params=params, timeout=20)
    response.raise_for_status()
    payload = response.json()
    products.extend(payload.get("products", []))

    while "rel=\"next\"" in response.headers.get("Link", ""):
        link_header = response.headers.get("Link", "")
        next_url = None
        for part in link_header.split(","):
            if "rel=\"next\"" in part:
                next_url = part.split(";")[0].strip().strip("<>")
                break
        if not next_url:
            break

        response = requests.get(next_url, headers=headers, timeout=20)
        response.raise_for_status()
        payload = response.json()
        products.extend(payload.get("products", []))

    return products


def _fetch_shopify_product_metafields(integration: Integration, shopify_product_id: str) -> list[dict[str, Any]]:
    """Fetch product metafields to detect Shopify Bundles component definitions."""
    creds = getattr(integration, "shopify_credentials", None)
    if not creds:
        return []

    endpoint = (
        f"{creds.store_url.rstrip('/')}/admin/api/{creds.api_version}"
        f"/products/{shopify_product_id}/metafields.json"
    )
    headers = {
        "X-Shopify-Access-Token": creds.access_token,
        "Content-Type": "application/json",
    }

    try:
        response = requests.get(endpoint, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json().get("metafields", [])
    except requests.RequestException:
        return []


def _get_or_create_category_by_name(name: str | None) -> Category:
    category_name = (name or "Uncategorized").strip() or "Uncategorized"
    category, _ = Category.objects.get_or_create(name=category_name)
    return category


def _is_bundle_product(shopify_product: dict[str, Any], metafields: list[dict[str, Any]]) -> bool:
    """Detect if Shopify product is a bundle.

    Detection strategy:
    1) Shopify Bundles metadata keys/namespaces
    2) Tags mentioning bundle
    3) product_type naming convention
    """
    tags = [tag.strip().lower() for tag in str(shopify_product.get("tags", "")).split(",") if tag.strip()]
    product_type = str(shopify_product.get("product_type", "")).lower()

    if any("bundle" in tag for tag in tags):
        return True
    if "bundle" in product_type:
        return True

    for metafield in metafields:
        namespace = str(metafield.get("namespace", "")).lower()
        key = str(metafield.get("key", "")).lower()
        if "bundle" in namespace or "bundle" in key:
            return True

    return False


def _parse_bundle_components(
    shopify_product: dict[str, Any],
    metafields: list[dict[str, Any]],
    variant_id_to_sku: dict[str, str],
    product_id_to_default_sku: dict[str, str],
) -> list[tuple[str, int]]:
    """Return bundle components as list[(component_sku, quantity)]."""
    components: list[tuple[str, int]] = []

    # Strategy A: Parse metafield JSON payloads from Shopify Bundles app.
    for metafield in metafields:
        value = metafield.get("value")
        if not value:
            continue

        try:
            parsed = value if isinstance(value, (list, dict)) else __import__("json").loads(value)
        except Exception:  # noqa: BLE001
            continue

        candidate_items: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            candidate_items = [item for item in parsed if isinstance(item, dict)]
        elif isinstance(parsed, dict):
            for key in ["components", "bundle_components", "items", "products"]:
                items = parsed.get(key)
                if isinstance(items, list):
                    candidate_items = [item for item in items if isinstance(item, dict)]
                    break

        for item in candidate_items:
            qty = int(item.get("quantity") or 1)
            sku = str(item.get("sku") or "").strip()

            if not sku:
                variant_id = str(item.get("variant_id") or item.get("variantId") or "").strip()
                product_id = str(item.get("product_id") or item.get("productId") or "").strip()
                if variant_id and variant_id in variant_id_to_sku:
                    sku = variant_id_to_sku[variant_id]
                elif product_id and product_id in product_id_to_default_sku:
                    sku = product_id_to_default_sku[product_id]

            if sku:
                components.append((sku, max(1, qty)))

    # Strategy B: Optional tag-based fallback: bundle:SKU1*2|SKU2*1
    tags = [tag.strip() for tag in str(shopify_product.get("tags", "")).split(",") if tag.strip()]
    bundle_tag = next((tag for tag in tags if tag.lower().startswith("bundle:")), None)
    if bundle_tag:
        raw = bundle_tag.split(":", 1)[1]
        for part in raw.replace(";", "|").split("|"):
            part = part.strip()
            if not part:
                continue
            if "*" in part:
                sku_part, qty_part = part.split("*", 1)
                sku = sku_part.strip()
                try:
                    qty = max(1, int(qty_part.strip()))
                except ValueError:
                    qty = 1
            else:
                sku = part.strip()
                qty = 1
            if sku:
                components.append((sku, qty))

    # De-duplicate and aggregate quantities per SKU.
    aggregated: dict[str, int] = {}
    for sku, qty in components:
        aggregated[sku] = aggregated.get(sku, 0) + qty

    return [(sku, qty) for sku, qty in aggregated.items()]


@transaction.atomic
def import_shopify_products(
    integration: Integration,
    owner: Any,
) -> dict[str, int]:
    """Dedicated Shopify product sync endpoint service.

    Handles both individual products and bundle products.
    Bundle composition is persisted to ProductBundle.
    """
    if integration.type != Integration.IntegrationType.SHOPIFY:
        raise ValueError("Only Shopify integrations support this import.")

    products_payload = cast(list[dict[str, Any]], _fetch_shopify_products(integration))

    imported = 0
    updated = 0
    skipped = 0
    bundles = 0

    # Build lookups from Shopify payload.
    variant_id_to_sku: dict[str, str] = {}
    product_id_to_default_sku: dict[str, str] = {}
    for p in products_payload:
        first_sku = ""
        variants = cast(list[dict[str, Any]], p.get("variants", []) or [])
        for v in variants:
            variant_id = str(v.get("id") or "").strip()
            sku = str(v.get("sku") or "").strip()
            if variant_id and sku:
                variant_id_to_sku[variant_id] = sku
            if sku and not first_sku:
                first_sku = sku
        product_id = str(p.get("id") or "").strip()
        if product_id and first_sku:
            product_id_to_default_sku[product_id] = first_sku

    # First pass: upsert all products by SKU.
    for p in products_payload:
        shopify_product_id = str(p.get("id") or "").strip()
        variants = cast(list[dict[str, Any]], p.get("variants", []) or [])
        if not variants:
            skipped += 1
            continue

        metafields = _fetch_shopify_product_metafields(integration, shopify_product_id)
        is_bundle = _is_bundle_product(p, metafields)

        for v in variants:
            sku = str(v.get("sku") or "").strip()
            if not sku:
                skipped += 1
                continue

            variant_title = str(v.get("title") or "").strip()
            base_name = str(p.get("title") or sku).strip()
            name = base_name if variant_title.lower() in {"", "default title"} else f"{base_name} - {variant_title}"

            category = _get_or_create_category_by_name(p.get("product_type"))
            image_url = ""
            images = cast(list[dict[str, Any]], p.get("images", []) or [])
            first_image = images[0] if images else None
            if isinstance(first_image, dict):
                image_url = str(first_image.get("src") or "")

            product, created = Product.objects.update_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "description": str(p.get("body_html") or ""),
                    "category": category,
                    "price": _as_decimal(v.get("price")),
                    "reorder_level": 10,
                    "image_url": image_url,
                    "is_bundle": is_bundle,
                },
            )

            if created:
                imported += 1
            else:
                updated += 1

            if is_bundle:
                bundles += 1

    # Second pass: build bundle composition for bundle products.
    for p in products_payload:
        shopify_product_id = str(p.get("id") or "").strip()
        variants = cast(list[dict[str, Any]], p.get("variants", []) or [])
        if not variants:
            continue

        metafields = _fetch_shopify_product_metafields(integration, shopify_product_id)
        if not _is_bundle_product(p, metafields):
            continue

        parent_variant = variants[0] if variants else {}
        parent_sku = str(parent_variant.get("sku") or "").strip()
        if not parent_sku:
            continue

        bundle_parent = cast(Product | None, Product.objects.filter(sku=parent_sku, is_bundle=True).first())
        if not bundle_parent:
            continue

        components = _parse_bundle_components(
            p,
            metafields,
            variant_id_to_sku=variant_id_to_sku,
            product_id_to_default_sku=product_id_to_default_sku,
        )

        # Replace bundle components atomically.
        ProductBundle.objects.filter(bundle=bundle_parent).delete()

        for component_sku, qty in components:
            component = cast(Product | None, Product.objects.filter(sku=component_sku, is_bundle=False).first())
            if not component:
                continue
            if component_sku == parent_sku:
                continue

            ProductBundle.objects.create(
                bundle=bundle_parent,
                component=component,
                quantity=max(1, qty),
            )

    integration.last_sync = timezone.now()
    integration.status = Integration.IntegrationStatus.ACTIVE
    integration.save(update_fields=["last_sync", "status", "updated_at"])

    return {
        "total": len(products_payload),
        "imported": imported,
        "updated": updated,
        "skipped": skipped,
        "bundles": bundles,
    }


def auto_sync_order_to_erp(order: Order) -> None:
    """Automatically push an order to all active auto-sync Odoo/QB integrations
    for its market. Called after order creation (webhook or manual import).

    Sync results are recorded as OrderSyncLog entries and the order's
    sync status fields are updated."""
    market_name = order.market.name if order.market_id else None
    if not market_name:
        return

    integrations = (
        Integration.objects
        .filter(
            market=market_name,
            auto_sync_orders=True,
            status=Integration.IntegrationStatus.ACTIVE,
        )
        .select_related('odoo_credentials', 'quickbooks_credentials')
    )

    for integration in integrations:
        if integration.type == Integration.IntegrationType.ODOO:
            _auto_sync_order_to_odoo(order, integration)
        elif integration.type == Integration.IntegrationType.QUICKBOOKS:
            _auto_sync_order_to_quickbooks(order, integration)


def _auto_sync_order_to_odoo(order: Order, integration: Integration) -> None:
    from .models import OrderSyncLog

    # Skip if already synced; correct status if IDs exist but status is stale
    if order.odoo_sales_order_id and order.odoo_sales_invoice_id:
        if order.odoo_sync_status != order.SYNC_SUCCESS:
            order.odoo_sync_status = order.SYNC_SUCCESS
            order.save(update_fields=['odoo_sync_status'])
        return

    order.odoo_sync_status = order.SYNC_PENDING
    order.save(update_fields=['odoo_sync_status'])

    # Create Sales Order
    if not order.odoo_sales_order_id:
        try:
            so_id = create_odoo_sales_order(integration, order)
            order.odoo_sales_order_id = str(so_id)
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_SO,
                status=OrderSyncLog.SyncStatus.SUCCESS,
                external_id=str(so_id),
            )
        except Exception as exc:  # noqa: BLE001
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_SO,
                status=OrderSyncLog.SyncStatus.FAILED,
                error_message=str(exc)[:5000],
            )
            order.odoo_sync_status = order.SYNC_FAILED
            order.save(update_fields=['odoo_sync_status', 'odoo_sales_order_id'])
            return

    # Create Invoice
    if not order.odoo_sales_invoice_id:
        try:
            inv_id = create_odoo_invoice_record(integration, order)
            order.odoo_sales_invoice_id = str(inv_id)
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_INVOICE,
                status=OrderSyncLog.SyncStatus.SUCCESS,
                external_id=str(inv_id),
            )
        except Exception as exc:  # noqa: BLE001
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_INVOICE,
                status=OrderSyncLog.SyncStatus.FAILED,
                error_message=str(exc)[:5000],
            )
            order.odoo_sync_status = order.SYNC_FAILED
            order.save(update_fields=['odoo_sync_status', 'odoo_sales_order_id', 'odoo_sales_invoice_id'])
            return

    order.odoo_sync_status = order.SYNC_SUCCESS
    order.save(update_fields=['odoo_sync_status', 'odoo_sales_order_id', 'odoo_sales_invoice_id'])


def _auto_sync_order_to_quickbooks(order: Order, integration: Integration) -> None:
    from .models import OrderSyncLog

    if order.quickbooks_sales_invoice_id:
        return

    order.quickbooks_sync_status = order.SYNC_PENDING
    order.save(update_fields=['quickbooks_sync_status'])

    try:
        invoice_id = create_quickbooks_sales_invoice(integration, order)
        order.quickbooks_sales_invoice_id = str(invoice_id)
        order.quickbooks_sync_status = order.SYNC_SUCCESS
        order.save(update_fields=['quickbooks_sync_status', 'quickbooks_sales_invoice_id'])
        OrderSyncLog.objects.create(
            order=order,
            integration=integration,
            target=OrderSyncLog.SyncTarget.QUICKBOOKS,
            status=OrderSyncLog.SyncStatus.SUCCESS,
            external_id=str(invoice_id),
        )
    except Exception as exc:  # noqa: BLE001
        order.quickbooks_sync_status = order.SYNC_FAILED
        order.save(update_fields=['quickbooks_sync_status'])
        OrderSyncLog.objects.create(
            order=order,
            integration=integration,
            target=OrderSyncLog.SyncTarget.QUICKBOOKS,
            status=OrderSyncLog.SyncStatus.FAILED,
            error_message=str(exc)[:5000],
        )


# ---------------------------------------------------------------------------
# Auto-UPDATE ERP records when a Shopify order is updated
# ---------------------------------------------------------------------------

def auto_update_order_in_erp(order: Order) -> None:
    """Automatically update existing ERP records (Odoo SO/Invoice, QB Invoice)
    when an order is updated via Shopify webhook.

    Only fires for integrations with auto_sync_orders=True and only if
    the order already has an external ID for the target platform.
    """
    market_name = order.market.name if order.market_id else None
    if not market_name:
        return

    # Only bother if the order already has at least one ERP record
    has_odoo = bool(order.odoo_sales_order_id or order.odoo_sales_invoice_id)
    has_qb = bool(order.quickbooks_sales_invoice_id)
    if not has_odoo and not has_qb:
        return

    integrations = (
        Integration.objects
        .filter(
            market=market_name,
            auto_sync_orders=True,
            status=Integration.IntegrationStatus.ACTIVE,
        )
        .select_related('odoo_credentials', 'quickbooks_credentials')
    )

    for integration in integrations:
        if integration.type == Integration.IntegrationType.ODOO and has_odoo:
            _auto_update_order_in_odoo(order, integration)
        elif integration.type == Integration.IntegrationType.QUICKBOOKS and has_qb:
            _auto_update_order_in_quickbooks(order, integration)


def _auto_update_order_in_odoo(order: Order, integration: Integration) -> None:
    from .models import OrderSyncLog

    order.odoo_sync_status = order.SYNC_PENDING
    order.save(update_fields=['odoo_sync_status'])

    # Update Sales Order
    if order.odoo_sales_order_id:
        try:
            update_odoo_sales_order(integration, order)
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_SO,
                status=OrderSyncLog.SyncStatus.SUCCESS,
                external_id=order.odoo_sales_order_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                'Auto-update Odoo SO failed for order %s', order.order_number,
            )
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_SO,
                status=OrderSyncLog.SyncStatus.FAILED,
                error_message=str(exc)[:5000],
            )
            order.odoo_sync_status = order.SYNC_FAILED
            order.save(update_fields=['odoo_sync_status'])
            return

    # Update Invoice
    if order.odoo_sales_invoice_id:
        try:
            update_odoo_invoice_record(integration, order)
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_INVOICE,
                status=OrderSyncLog.SyncStatus.SUCCESS,
                external_id=order.odoo_sales_invoice_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                'Auto-update Odoo Invoice failed for order %s', order.order_number,
            )
            OrderSyncLog.objects.create(
                order=order,
                integration=integration,
                target=OrderSyncLog.SyncTarget.ODOO_INVOICE,
                status=OrderSyncLog.SyncStatus.FAILED,
                error_message=str(exc)[:5000],
            )
            order.odoo_sync_status = order.SYNC_FAILED
            order.save(update_fields=['odoo_sync_status'])
            return

    order.odoo_sync_status = order.SYNC_SUCCESS
    order.save(update_fields=['odoo_sync_status'])


def _auto_update_order_in_quickbooks(order: Order, integration: Integration) -> None:
    from .models import OrderSyncLog

    if not order.quickbooks_sales_invoice_id:
        return

    order.quickbooks_sync_status = order.SYNC_PENDING
    order.save(update_fields=['quickbooks_sync_status'])

    try:
        update_quickbooks_invoice(integration, order)
        order.quickbooks_sync_status = order.SYNC_SUCCESS
        order.save(update_fields=['quickbooks_sync_status'])
        OrderSyncLog.objects.create(
            order=order,
            integration=integration,
            target=OrderSyncLog.SyncTarget.QUICKBOOKS,
            status=OrderSyncLog.SyncStatus.SUCCESS,
            external_id=order.quickbooks_sales_invoice_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            'Auto-update QuickBooks Invoice failed for order %s', order.order_number,
        )
        order.quickbooks_sync_status = order.SYNC_FAILED
        order.save(update_fields=['quickbooks_sync_status'])
        OrderSyncLog.objects.create(
            order=order,
            integration=integration,
            target=OrderSyncLog.SyncTarget.QUICKBOOKS,
            status=OrderSyncLog.SyncStatus.FAILED,
            error_message=str(exc)[:5000],
        )
