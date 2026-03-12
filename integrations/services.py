"""Integration service helpers for connection checks and Shopify sync."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from xmlrpc import client as xmlrpc_client
import base64
import hashlib
import hmac

import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from orders.models import Order, OrderItem
from products.models import Product, Category, ProductBundle, Market

from .models import Integration, ShopifyWebhookDelivery


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
        common = xmlrpc_client.ServerProxy(
            f"{creds.server_url.rstrip('/')}/xmlrpc/2/common"
        )
        uid = common.authenticate(
            creds.database_url,
            creds.email,
            creds.api_key,
            {},
        )
    except Exception as exc:  # noqa: BLE001
        return False, f'Odoo connection failed: {exc}'

    if uid:
        return True, 'Odoo connection succeeded.'
    return False, 'Odoo authentication failed. Check database/email/API key.'


def _test_quickbooks(integration: Integration):
    creds = getattr(integration, 'quickbooks_credentials', None)
    if not creds:
        return False, 'QuickBooks credentials not configured.'

    # OAuth handshake requires user authorization. We validate required fields only.
    if creds.realm_id and creds.client_id and creds.client_key:
        return True, 'QuickBooks credentials are configured. Complete OAuth authorization to finish setup.'

    return False, 'QuickBooks credentials are incomplete.'


def test_integration_connection(integration: Integration):
    """Dispatch connection checks by integration type."""

    if integration.type == Integration.IntegrationType.SHOPIFY:
        return _test_shopify(integration)

    if integration.type == Integration.IntegrationType.ODOO:
        return _test_odoo(integration)

    if integration.type == Integration.IntegrationType.QUICKBOOKS:
        return _test_quickbooks(integration)

    return False, 'Unsupported integration type.'


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
    2) tags contain origin:sukhiba -> WHATSAPP
    3) default -> WEBSITE
    """
    if _is_pos_order(order_payload):
        return Order.CHANNEL_POS

    tags = _extract_tags(order_payload)
    if "origin:sukhiba" in tags:
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


def _parse_datetime(value: Any):
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
    if not raw_body or not hmac_header or not secret:
        return False

    digest = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).digest()
    computed_hmac = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(computed_hmac, hmac_header)


def _resolve_owner_for_integration(integration: Integration):
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
    owner,
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


def _get_or_create_category_by_name(name: str | None):
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
    owner,
) -> dict[str, int]:
    """Dedicated Shopify product sync endpoint service.

    Handles both individual products and bundle products.
    Bundle composition is persisted to ProductBundle.
    """
    if integration.type != Integration.IntegrationType.SHOPIFY:
        raise ValueError("Only Shopify integrations support this import.")

    products_payload = _fetch_shopify_products(integration)

    imported = 0
    updated = 0
    skipped = 0
    bundles = 0

    # Build lookups from Shopify payload.
    variant_id_to_sku: dict[str, str] = {}
    product_id_to_default_sku: dict[str, str] = {}
    for p in products_payload:
        first_sku = ""
        for v in p.get("variants", []) or []:
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
        variants = p.get("variants", []) or []
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
            images = p.get("images", []) or []
            if images and isinstance(images[0], dict):
                image_url = str(images[0].get("src") or "")

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
        variants = p.get("variants", []) or []
        if not variants:
            continue

        metafields = _fetch_shopify_product_metafields(integration, shopify_product_id)
        if not _is_bundle_product(p, metafields):
            continue

        parent_sku = str((variants[0] or {}).get("sku") or "").strip()
        if not parent_sku:
            continue

        bundle_parent = Product.objects.filter(sku=parent_sku, is_bundle=True).first()
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
            component = Product.objects.filter(sku=component_sku, is_bundle=False).first()
            if not component:
                continue
            if component.id == bundle_parent.id:
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
