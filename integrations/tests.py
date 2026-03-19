# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportMissingTypeArgument=false, reportArgumentType=false, reportPrivateUsage=false
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

import base64
import hashlib
import hmac
import json

from authentication.models import UserRole
from integrations.models import (
    Integration,
    QuickBooksCredentials,
    ShopifyCredentials,
    ShopifyWebhookDelivery,
)
from integrations.services import _resolve_order_channel, _normalize_market_and_currency
from integrations.services import _resolve_configured_odoo_partner_id
from integrations.services import (
    _resolve_configured_quickbooks_customer_id,
    _fetch_qb_sku_map,
    _resolve_qb_item_ref,
    create_quickbooks_sales_invoice,
)
from orders.models import Order
from products.models import Product, Category, Market, Warehouse

User = get_user_model()


class IntegrationApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='owner@example.com',
            password='secret123',
            role=UserRole.OWNER,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.warehouse = Warehouse.objects.create(
            name='Nairobi Hub',
            location='Nairobi',
            address='Nairobi, Kenya',
            capacity=10000,
            manager=self.user,
        )

    def test_create_odoo_integration_with_required_credentials(self):
        response = self.client.post(
            '/api/integrations/',
            {
                'name': 'Nigeria Odoo',
                'type': 'ODOO',
                'market': 'Nigeria',
                'status': 'ACTIVE',
                'warehouse': self.warehouse.id,
                'credentials': {
                    'server_url': 'https://odoo.example.com',
                    'database_url': 'odoo_prod_ng',
                    'company_id': 'company-ng-001',
                    'email': 'ops@example.com',
                    'api_key': 'odoo-api-key',
                    'sukhiba_partner_id': '101',
                    'pos_partner_id': '102',
                    'ecommerce_partner_id': '103',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['type'], 'ODOO')
        self.assertEqual(response.data['market'], 'Nigeria')
        self.assertIn('credential_summary', response.data)
        self.assertEqual(
            response.data['credential_summary']['company_id'],
            'company-ng-001',
        )
        self.assertEqual(response.data['credential_summary']['sukhiba_partner_id'], '101')
        self.assertEqual(response.data['credential_summary']['pos_partner_id'], '102')
        self.assertEqual(response.data['credential_summary']['ecommerce_partner_id'], '103')

    def test_reject_missing_odoo_company_id(self):
        response = self.client.post(
            '/api/integrations/',
            {
                'name': 'Nigeria Odoo',
                'type': 'ODOO',
                'market': 'Nigeria',
                'credentials': {
                    'server_url': 'https://odoo.example.com',
                    'database_url': 'odoo_prod_ng',
                    'email': 'ops@example.com',
                    'api_key': 'odoo-api-key',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('credentials', response.data)

    def test_reject_missing_quickbooks_fields(self):
        response = self.client.post(
            '/api/integrations/',
            {
                'name': 'Nigeria QuickBooks',
                'type': 'QUICKBOOKS',
                'market': 'Nigeria',
                'credentials': {
                    'realm_id': '1234',
                    'client_id': 'client-id-only',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('credentials', response.data)

    def test_unique_integration_type_per_market_constraint(self):
        payload = {
            'name': 'Nigeria Shopify',
            'type': 'SHOPIFY',
            'market': 'Nigeria',
            'credentials': {
                'store_url': 'https://shop.example.com',
                'access_token': 'token-1',
                'api_key': 'shopify-api-key',
                'api_secret': 'shopify-api-secret',
            },
        }

        first = self.client.post('/api/integrations/', payload, format='json')
        second = self.client.post('/api/integrations/', payload, format='json')

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_can_edit_integration_type_with_new_credentials(self):
        created = self.client.post(
            '/api/integrations/',
            {
                'name': 'Nigeria Shopify',
                'type': 'SHOPIFY',
                'market': 'Nigeria',
                'credentials': {
                    'store_url': 'https://shop.example.com',
                    'access_token': 'token-1',
                    'api_key': 'shopify-api-key',
                    'api_secret': 'shopify-api-secret',
                },
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        integration_id = created.data['id']
        response = self.client.patch(
            f'/api/integrations/{integration_id}/',
            {
                'type': 'QUICKBOOKS',
                'credentials': {
                    'realm_id': 'realm-123',
                    'client_id': 'client-123',
                    'client_key': 'key-123',
                    'sukhiba_customer_id': '201',
                    'pos_customer_id': '202',
                    'ecommerce_customer_id': '203',
                    'environment': 'SANDBOX',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['type'], 'QUICKBOOKS')
        self.assertEqual(
            response.data['credential_summary']['environment'],
            'SANDBOX',
        )

    def test_can_patch_odoo_partner_ids_without_resubmitting_all_credentials(self):
        created = self.client.post(
            '/api/integrations/',
            {
                'name': 'Kenya Odoo',
                'type': 'ODOO',
                'market': 'Kenya',
                'credentials': {
                    'server_url': 'https://odoo.example.com',
                    'database_url': 'odoo-db',
                    'company_id': '1',
                    'email': 'admin@example.com',
                    'api_key': 'odoo-key',
                    'sukhiba_partner_id': '1001',
                    'pos_partner_id': '1002',
                    'ecommerce_partner_id': '1003',
                },
            },
            format='json',
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)

        integration_id = created.data['id']
        response = self.client.patch(
            f'/api/integrations/{integration_id}/',
            {
                'credentials': {
                    'sukhiba_partner_id': '2001',
                    'pos_partner_id': '2002',
                    'ecommerce_partner_id': '2003',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['credential_summary']['sukhiba_partner_id'], '2001')
        self.assertEqual(response.data['credential_summary']['pos_partner_id'], '2002')
        self.assertEqual(response.data['credential_summary']['ecommerce_partner_id'], '2003')

    def test_sync_endpoint_updates_last_sync(self):
        integration = Integration.objects.create(
            name='Kenya QuickBooks',
            type='QUICKBOOKS',
            market='Kenya',
            status='ACTIVE',
        )

        # create credentials directly for quickbooks test-connection compatibility
        QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client',
            client_key='key',
        )

        response = self.client.post(f'/api/integrations/{integration.id}/sync/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        integration.refresh_from_db()
        self.assertIsNotNone(integration.last_sync)

    def test_test_connection_for_quickbooks_configured(self):
        integration = Integration.objects.create(
            name='Kenya QuickBooks',
            type='QUICKBOOKS',
            market='Kenya',
            status='ACTIVE',
            warehouse=self.warehouse,
        )
        from django.utils import timezone
        from datetime import timedelta
        QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client',
            client_key='key',
            access_token='valid-token',
            refresh_token='refresh-token',
            token_expiry=timezone.now() + timedelta(hours=1),
        )

        with patch('integrations.services.requests.request') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                'CompanyInfo': {'CompanyName': 'Test Company'}
            }
            mock_req.return_value = mock_resp

            response = self.client.post(
                f'/api/integrations/{integration.id}/test-connection/'
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])
        self.assertIn('Test Company', response.data['message'])

    def test_reject_missing_shopify_api_secret(self):
        response = self.client.post(
            '/api/integrations/',
            {
                'name': 'Kenya Shopify',
                'type': 'SHOPIFY',
                'market': 'Kenya',
                'credentials': {
                    'store_url': 'https://kenya-shop.myshopify.com',
                    'access_token': 'token-1',
                    'api_key': 'shopify-api-key',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('credentials', response.data)


class ShopifyOrderMappingRulesTests(APITestCase):
    def setUp(self):
        self.integration_kenya = Integration.objects.create(
            name='Kenya Shopify',
            type='SHOPIFY',
            market='Kenya',
            status='ACTIVE',
        )
        self.integration_nigeria = Integration.objects.create(
            name='Nigeria Shopify',
            type='SHOPIFY',
            market='Nigeria',
            status='ACTIVE',
        )

    def test_default_channel_is_website(self):
        payload = {'tags': 'vip, repeat-customer'}
        self.assertEqual(_resolve_order_channel(payload), 'WEBSITE')

    def test_sukhiba_tag_maps_to_whatsapp(self):
        payload = {'tags': 'vip, origin:sukhiba, repeat-customer'}
        self.assertEqual(_resolve_order_channel(payload), 'WHATSAPP')

    def test_pos_order_maps_to_pos(self):
        payload = {'source_name': 'pos', 'tags': 'origin:sukhiba'}
        self.assertEqual(_resolve_order_channel(payload), 'POS')

    def test_kenyan_integration_forces_market_and_currency(self):
        payload = {'currency': 'USD'}
        market, currency = _normalize_market_and_currency(self.integration_kenya, payload)
        self.assertEqual(market, 'Kenya')
        self.assertEqual(currency, 'KES')

    def test_non_kenyan_uses_integration_market_and_shopify_currency(self):
        payload = {'currency': 'NGN'}
        market, currency = _normalize_market_and_currency(self.integration_nigeria, payload)
        self.assertEqual(market, 'Nigeria')
        self.assertEqual(currency, 'NGN')


class OdooPartnerRoutingRulesTests(APITestCase):
    def setUp(self):
        class Creds:
            sukhiba_partner_id = '1001'
            pos_partner_id = '1002'
            ecommerce_partner_id = '1003'

        self.creds = Creds()

    def test_origin_sukhiba_tag_uses_sukhiba_partner(self):
        class OrderStub:
            shopify_tags = 'vip, origin:sukhiba, repeat-customer'
            order_channel = Order.CHANNEL_WEBSITE

        self.assertEqual(_resolve_configured_odoo_partner_id(self.creds, OrderStub()), 1001)

    def test_pos_channel_uses_pos_partner_when_no_sukhiba_tag(self):
        class OrderStub:
            shopify_tags = 'vip, repeat-customer'
            order_channel = Order.CHANNEL_POS

        self.assertEqual(_resolve_configured_odoo_partner_id(self.creds, OrderStub()), 1002)

    def test_default_uses_ecommerce_partner(self):
        class OrderStub:
            shopify_tags = ''
            order_channel = Order.CHANNEL_WEBSITE

        self.assertEqual(_resolve_configured_odoo_partner_id(self.creds, OrderStub()), 1003)

    def test_sukhiba_tag_takes_precedence_over_pos_channel(self):
        class OrderStub:
            shopify_tags = 'origin:sukhiba'
            order_channel = Order.CHANNEL_POS

        self.assertEqual(_resolve_configured_odoo_partner_id(self.creds, OrderStub()), 1001)

    def test_missing_selected_partner_id_raises_error(self):
        class Creds:
            sukhiba_partner_id = ''
            pos_partner_id = '1002'
            ecommerce_partner_id = '1003'

        class OrderStub:
            shopify_tags = 'origin:sukhiba'
            order_channel = Order.CHANNEL_WEBSITE

        with self.assertRaisesMessage(ValueError, 'Missing configured Odoo partner ID'):
            _resolve_configured_odoo_partner_id(Creds(), OrderStub())

    def test_non_numeric_partner_id_raises_error(self):
        class Creds:
            sukhiba_partner_id = 'abc'
            pos_partner_id = '1002'
            ecommerce_partner_id = '1003'

        class OrderStub:
            shopify_tags = 'origin:sukhiba'
            order_channel = Order.CHANNEL_WEBSITE

        with self.assertRaisesMessage(ValueError, 'must be numeric'):
            _resolve_configured_odoo_partner_id(Creds(), OrderStub())


class QuickBooksCustomerRoutingRulesTests(APITestCase):
    def setUp(self):
        class Creds:
            sukhiba_customer_id = '3001'
            pos_customer_id = '3002'
            ecommerce_customer_id = '3003'

        self.creds = Creds()

    def test_origin_sukhiba_tag_uses_sukhiba_customer(self):
        class OrderStub:
            shopify_tags = 'vip, origin:sukhiba, repeat-customer'
            order_channel = Order.CHANNEL_WEBSITE

        self.assertEqual(_resolve_configured_quickbooks_customer_id(self.creds, OrderStub()), '3001')

    def test_pos_channel_uses_pos_customer_when_no_sukhiba_tag(self):
        class OrderStub:
            shopify_tags = 'vip, repeat-customer'
            order_channel = Order.CHANNEL_POS

        self.assertEqual(_resolve_configured_quickbooks_customer_id(self.creds, OrderStub()), '3002')

    def test_default_uses_ecommerce_customer(self):
        class OrderStub:
            shopify_tags = ''
            order_channel = Order.CHANNEL_WEBSITE

        self.assertEqual(_resolve_configured_quickbooks_customer_id(self.creds, OrderStub()), '3003')

    def test_missing_selected_customer_id_raises_error(self):
        class Creds:
            sukhiba_customer_id = ''
            pos_customer_id = '3002'
            ecommerce_customer_id = '3003'

        class OrderStub:
            shopify_tags = 'origin:sukhiba'
            order_channel = Order.CHANNEL_WEBSITE

        with self.assertRaisesMessage(ValueError, 'Missing configured QuickBooks customer ID'):
            _resolve_configured_quickbooks_customer_id(Creds(), OrderStub())


class QuickBooksInvoiceCreationTests(APITestCase):
    @patch('integrations.services.requests.request')
    @patch('integrations.services._ensure_quickbooks_token', return_value='valid-token')
    def test_create_quickbooks_sales_invoice_uses_product_item_ref(self, _token_mock, request_mock):
        class QuickBooksCreds:
            realm_id = 'realm-001'
            client_id = 'client-001'
            client_key = 'token-001'
            environment = 'SANDBOX'
            sukhiba_customer_id = '7001'
            pos_customer_id = '7002'
            ecommerce_customer_id = '7003'
            access_token = 'valid-token'
            refresh_token = 'refresh-token'
            token_expiry = None
            tax_id = ''
            shipping_fee_account_id = ''
            invoice_prefix = ''
            api_base_url = 'https://sandbox-quickbooks.api.intuit.com'
            default_product_id = 'QB-PROD-001'

        class IntegrationStub:
            id = 1
            quickbooks_credentials = QuickBooksCreds()

        class ItemStub:
            product_name = 'Test Product'
            quantity = 2
            unit_price = '10.00'
            total_price = '20.00'
            product_id = 1
            product = None
            sku = 'SKU-501'

        class ItemManagerStub:
            @staticmethod
            def all():
                return [ItemStub()]

        class OrderStub:
            order_number = 'ORD-1001'
            shopify_order_number = '#1001'
            shopify_tags = 'origin:sukhiba'
            order_channel = Order.CHANNEL_WEBSITE
            items = ItemManagerStub()
            market_id = None
            shipping_price = 0

        sku_map_response = MagicMock()
        sku_map_response.status_code = 200
        sku_map_response.json.return_value = {
            'QueryResponse': {'Item': [{'Name': 'Test Product', 'Sku': 'SKU-501', 'Id': 'QB-ITEM-1'}]}
        }

        invoice_response = MagicMock()
        invoice_response.status_code = 200
        invoice_response.json.return_value = {'Invoice': {'Id': 'INV-9001'}}

        request_mock.side_effect = [sku_map_response, invoice_response]

        invoice_id = create_quickbooks_sales_invoice(IntegrationStub(), OrderStub())

        self.assertEqual(invoice_id, 'INV-9001')
        _, kwargs = request_mock.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer valid-token')
        self.assertEqual(kwargs['json']['CustomerRef']['value'], '7001')
        # DocNumber derived from shopify_order_number ('#1001') with '#' stripped, no prefix
        self.assertEqual(kwargs['json']['DocNumber'], '1001')
        self.assertEqual(
            kwargs['json']['Line'][0]['SalesItemLineDetail']['ItemRef']['value'],
            'QB-ITEM-1',
        )

    @patch('integrations.services.requests.request')
    @patch('integrations.services._ensure_quickbooks_token', return_value='valid-token')
    def test_quickbooks_invoice_includes_prefix_tax_shipping(self, _token_mock, request_mock):
        """Verify invoice creation includes prefix, tax code, and shipping line."""
        class QuickBooksCreds:
            realm_id = 'realm-001'
            client_id = 'client-001'
            client_key = 'token-001'
            environment = 'SANDBOX'
            sukhiba_customer_id = '7001'
            pos_customer_id = '7002'
            ecommerce_customer_id = '7003'
            access_token = 'valid-token'
            refresh_token = 'refresh-token'
            token_expiry = None
            tax_id = 'TAX-001'
            shipping_fee_account_id = 'SHIP-001'
            invoice_prefix = 'SHT'
            api_base_url = 'https://sandbox-quickbooks.api.intuit.com'
            default_product_id = 'QB-PROD-001'

        class IntegrationStub:
            id = 1
            quickbooks_credentials = QuickBooksCreds()

        class ItemStub:
            product_name = 'Aloe Gel'
            quantity = 2
            unit_price = '15.00'
            total_price = '30.00'
            product_id = 1
            product = None
            sku = 'SKU-501'

        class ItemManagerStub:
            @staticmethod
            def all():
                return [ItemStub()]

        class OrderStub:
            order_number = 'ORD-5555'
            shopify_order_number = '#5555'
            shopify_tags = ''
            order_channel = Order.CHANNEL_WEBSITE
            items = ItemManagerStub()
            market_id = None
            shipping_price = '5.00'

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {'Invoice': {'Id': 'INV-5555'}}
        request_mock.return_value = response

        invoice_id = create_quickbooks_sales_invoice(IntegrationStub(), OrderStub())
        self.assertEqual(invoice_id, 'INV-5555')

        _, kwargs = request_mock.call_args
        payload = kwargs['json']

        # DocNumber = prefix + shopify_order_number with leading '#' stripped
        self.assertEqual(payload['DocNumber'], 'SHT5555')

        # Tax code on product line
        self.assertEqual(
            payload['Line'][0]['SalesItemLineDetail']['TaxCodeRef']['value'],
            'TAX-001',
        )

        # Shipping line item
        shipping_line = payload['Line'][-1]
        self.assertEqual(shipping_line['Description'], 'Shipping')
        self.assertEqual(shipping_line['Amount'], 5.0)
        self.assertEqual(
            shipping_line['SalesItemLineDetail']['ItemRef']['value'],
            'SHIP-001',
        )
        self.assertEqual(
            shipping_line['SalesItemLineDetail']['TaxCodeRef']['value'],
            'TAX-001',
        )


class FetchQbSkuMapTests(APITestCase):
    """Unit tests for _fetch_qb_sku_map."""

    def _make_creds(self):
        class Creds:
            api_base_url = 'https://sandbox-quickbooks.api.intuit.com'
            realm_id = 'realm-001'
        return Creds()

    @patch('integrations.services.requests.request')
    @patch('integrations.services._ensure_quickbooks_token', return_value='tok')
    def test_maps_sku_field_over_name(self, _, req_mock):
        """Item Sku field takes priority over Name when building the map."""
        req_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'QueryResponse': {
                    'Item': [{'Id': '42', 'Name': 'Aloe Gel', 'Sku': 'SKU-501'}]
                }
            },
        )
        result = _fetch_qb_sku_map(self._make_creds())
        self.assertEqual(result['sku-501'], '42')   # matched via Sku field
        self.assertEqual(result['aloe gel'], '42')  # Name still present as fallback

    @patch('integrations.services.requests.request')
    @patch('integrations.services._ensure_quickbooks_token', return_value='tok')
    def test_name_used_when_no_sku_field(self, _, req_mock):
        """When QBO item has no Sku field, Name is used as the only key."""
        req_mock.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'QueryResponse': {
                    'Item': [{'Id': '99', 'Name': 'SKU-999'}]
                }
            },
        )
        result = _fetch_qb_sku_map(self._make_creds())
        self.assertEqual(result['sku-999'], '99')

    @patch('integrations.services.requests.request')
    @patch('integrations.services._ensure_quickbooks_token', return_value='tok')
    def test_non_200_returns_empty_map(self, _, req_mock):
        req_mock.return_value = MagicMock(status_code=503)
        result = _fetch_qb_sku_map(self._make_creds())
        self.assertEqual(result, {})


class ResolveQbItemRefTests(APITestCase):
    """Unit tests for _resolve_qb_item_ref."""

    def test_exact_sku_match_returns_value(self):
        sku_map = {'sku-501': 'QB-ITEM-1', 'aloe gel': 'QB-ITEM-1'}
        ref = _resolve_qb_item_ref('SKU-501', 'Aloe Gel', sku_map, 'DEFAULT-ID')
        self.assertEqual(ref, {'value': 'QB-ITEM-1'})

    def test_case_insensitive_match(self):
        sku_map = {'sku-501': 'QB-ITEM-1'}
        ref = _resolve_qb_item_ref('sku-501', '', sku_map, 'DEFAULT-ID')
        self.assertIsNotNone(ref)
        self.assertEqual(ref['value'], 'QB-ITEM-1')

    def test_fallback_to_name(self):
        sku_map = {'aloe gel': 'QB-ITEM-1'}
        # SKU is missing from the map, but name is present
        ref = _resolve_qb_item_ref('SKU-UNKNOWN', 'Aloe Gel', sku_map, 'DEFAULT-ID')
        self.assertIsNotNone(ref)
        self.assertEqual(ref['value'], 'QB-ITEM-1')

    def test_missing_sku_falls_back_to_default(self):
        ref = _resolve_qb_item_ref('SKU-UNKNOWN', 'Unknown', {}, 'DEFAULT-ID')
        self.assertEqual(ref, {'value': 'DEFAULT-ID'})

    def test_no_sku_no_default_returns_none(self):
        ref = _resolve_qb_item_ref('', '', {}, '')
        self.assertIsNone(ref)

    def test_no_match_no_default_returns_none(self):
        ref = _resolve_qb_item_ref('SKU-MISSING', 'Product', {'other-sku': '5'}, '')
        self.assertIsNone(ref)


class IntegrationPermissionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email='owner-permissions@example.com',
            password='secret123',
            role=UserRole.OWNER,
        )
        self.manager = User.objects.create_user(
            email='manager-permissions@example.com',
            password='secret123',
            role=UserRole.MANAGER,
        )
        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)
        self.manager_client = APIClient()
        self.manager_client.force_authenticate(user=self.manager)
        self.warehouse = Warehouse.objects.create(
            name='Permissions Warehouse',
            location='Nairobi',
            address='Nairobi, Kenya',
            capacity=10000,
            manager=self.owner,
        )

    def test_owner_can_manage_integrations(self):
        response = self.owner_client.post(
            '/api/integrations/',
            {
                'name': 'Owner Shopify',
                'type': 'SHOPIFY',
                'market': 'Kenya',
                'warehouse': self.warehouse.id,
                'credentials': {
                    'store_url': 'https://shop.example.com',
                    'access_token': 'token-1',
                    'api_key': 'shopify-api-key',
                    'api_secret': 'shopify-api-secret',
                },
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_manager_cannot_manage_integrations(self):
        response = self.manager_client.get('/api/integrations/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ShopifyWebhookApiTests(APITestCase):
    def setUp(self):
        self.webhook_secret = 'test-webhook-secret'
        self.user = User.objects.create_user(
            email='manager@example.com',
            password='secret123',
        )
        self.warehouse = Warehouse.objects.create(
            name='Webhook Warehouse',
            location='Nairobi',
            address='Nairobi, Kenya',
            capacity=10000,
            manager=self.user,
        )
        self.integration = Integration.objects.create(
            name='Kenya Shopify',
            type='SHOPIFY',
            market='Kenya',
            status='ACTIVE',
            warehouse=self.warehouse,
        )
        ShopifyCredentials.objects.create(
            integration=self.integration,
            store_url='https://kenya-shop.myshopify.com',
            access_token='shopify-token',
            api_key='shopify-api-key',
            api_secret=self.webhook_secret,
            api_version='2024-01',
        )

    def _signed_post(
        self,
        url: str,
        payload: dict,
        topic: str,
        shop_domain='kenya-shop.myshopify.com',
        webhook_id='wh_default_1',
    ):
        raw = json.dumps(payload).encode('utf-8')
        signature = base64.b64encode(
            hmac.new(
                self.webhook_secret.encode('utf-8'),
                raw,
                hashlib.sha256,
            ).digest()
        ).decode('utf-8')

        return self.client.post(
            url,
            data=raw,
            content_type='application/json',
            HTTP_X_SHOPIFY_TOPIC=topic,
            HTTP_X_SHOPIFY_SHOP_DOMAIN=shop_domain,
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
            HTTP_X_SHOPIFY_WEBHOOK_ID=webhook_id,
        )

    def test_orders_create_webhook_creates_order(self):
        payload = {
            'id': 99001,
            'order_number': 4501,
            'currency': 'USD',
            'email': 'buyer@example.com',
            'customer': {'first_name': 'Jane', 'last_name': 'Doe', 'id': 55},
            'financial_status': 'paid',
            'fulfillment_status': None,
            'subtotal_price': '90.00',
            'total_tax': '10.00',
            'total_discounts': '0.00',
            'total_price': '100.00',
            'shipping_lines': [{'title': 'Standard', 'price': '5.00'}],
            'shipping_address': {'address1': 'Road 1', 'city': 'Nairobi', 'country': 'Kenya', 'country_code': 'KE'},
            'billing_address': {'address1': 'Road 1', 'city': 'Nairobi', 'country': 'Kenya'},
            'line_items': [
                {
                    'id': 1,
                    'sku': 'SKU-1',
                    'title': 'Aloe Gel',
                    'variant_title': 'Default Title',
                    'quantity': 2,
                    'price': '50.00',
                    'requires_shipping': True,
                    'gift_card': False,
                    'grams': 100,
                }
            ],
        }

        response = self._signed_post(
            '/api/webhooks/shopify/orders/create/',
            payload,
            topic='orders/create',
            webhook_id='wh_order_create_99001',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get(shopify_order_id='99001')
        self.assertEqual(order.market.name, 'Kenya')
        self.assertEqual(order.currency, 'KES')
        self.assertTrue(
            ShopifyWebhookDelivery.objects.filter(
                webhook_id='wh_order_create_99001',
                status=ShopifyWebhookDelivery.Status.PROCESSED,
            ).exists()
        )

    def test_duplicate_webhook_delivery_is_ignored(self):
        payload = {
            'id': 70001,
            'order_number': 70001,
            'currency': 'USD',
            'email': 'buyer@example.com',
            'customer': {'first_name': 'Dup', 'last_name': 'Test', 'id': 12},
            'financial_status': 'paid',
            'subtotal_price': '50.00',
            'total_tax': '0.00',
            'total_discounts': '0.00',
            'total_price': '50.00',
            'shipping_lines': [{'title': 'Standard', 'price': '0.00'}],
            'shipping_address': {'address1': 'Main', 'city': 'Nairobi', 'country': 'Kenya', 'country_code': 'KE'},
            'billing_address': {'address1': 'Main', 'city': 'Nairobi', 'country': 'Kenya'},
            'line_items': [{'id': 1, 'sku': 'SKU-DUP', 'title': 'Dup Product', 'quantity': 1, 'price': '50.00'}],
        }

        first = self._signed_post(
            '/api/webhooks/shopify/orders/create/',
            payload,
            topic='orders/create',
            webhook_id='wh_dup_70001',
        )
        second = self._signed_post(
            '/api/webhooks/shopify/orders/create/',
            payload,
            topic='orders/create',
            webhook_id='wh_dup_70001',
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Order.objects.count(), 1)
        self.assertTrue(second.data.get('duplicate', False))

    def test_missing_webhook_id_header_is_rejected(self):
        payload = {'id': 333}
        raw = json.dumps(payload).encode('utf-8')
        signature = base64.b64encode(
            hmac.new(
                self.webhook_secret.encode('utf-8'),
                raw,
                hashlib.sha256,
            ).digest()
        ).decode('utf-8')

        response = self.client.post(
            '/api/webhooks/shopify/orders/create/',
            data=raw,
            content_type='application/json',
            HTTP_X_SHOPIFY_TOPIC='orders/create',
            HTTP_X_SHOPIFY_SHOP_DOMAIN='kenya-shop.myshopify.com',
            HTTP_X_SHOPIFY_HMAC_SHA256=signature,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_orders_updated_webhook_updates_existing_order(self):
        create_payload = {
            'id': 88001,
            'order_number': 2001,
            'currency': 'USD',
            'email': 'buyer@example.com',
            'customer': {'first_name': 'John', 'last_name': 'Doe', 'id': 77},
            'financial_status': 'pending',
            'subtotal_price': '40.00',
            'total_tax': '0.00',
            'total_discounts': '0.00',
            'total_price': '40.00',
            'shipping_lines': [{'title': 'Standard', 'price': '0.00'}],
            'shipping_address': {'address1': 'Main', 'city': 'Nairobi', 'country': 'Kenya', 'country_code': 'KE'},
            'billing_address': {'address1': 'Main', 'city': 'Nairobi', 'country': 'Kenya'},
            'line_items': [{'id': 1, 'sku': 'SKU-2', 'title': 'Mask', 'quantity': 1, 'price': '40.00'}],
        }
        update_payload = {
            **create_payload,
            'financial_status': 'paid',
            'line_items': [{'id': 2, 'sku': 'SKU-2', 'title': 'Mask', 'quantity': 2, 'price': '40.00'}],
        }

        first = self._signed_post('/api/webhooks/shopify/orders/create/', create_payload, topic='orders/create', webhook_id='wh_order_88001_create')
        second = self._signed_post('/api/webhooks/shopify/orders/updated/', update_payload, topic='orders/updated', webhook_id='wh_order_88001_update')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(Order.objects.count(), 1)
        order = Order.objects.get(shopify_order_id='88001')
        self.assertEqual(order.payment_status, Order.PAID)
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().quantity, 2)

    def test_products_create_and_update_webhook_upserts_product(self):
        create_payload = {
            'id': 7771,
            'title': 'Daily Cleanser',
            'body_html': '<p>Gentle cleanser</p>',
            'product_type': 'Skincare',
            'tags': 'cleanser',
            'images': [{'src': 'https://cdn.example.com/cleanser.jpg'}],
            'variants': [
                {'id': 901, 'title': 'Default Title', 'sku': 'SKU-CLEANSER', 'price': '15.00'}
            ],
        }
        update_payload = {
            **create_payload,
            'title': 'Daily Cleanser Pro',
            'variants': [
                {'id': 901, 'title': 'Default Title', 'sku': 'SKU-CLEANSER', 'price': '17.00'}
            ],
        }

        first = self._signed_post('/api/webhooks/shopify/products/create/', create_payload, topic='products/create', webhook_id='wh_product_7771_create')
        second = self._signed_post('/api/webhooks/shopify/products/updated/', update_payload, topic='products/updated', webhook_id='wh_product_7771_update')

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)

        product = Product.objects.get(sku='SKU-CLEANSER')
        self.assertEqual(product.name, 'Daily Cleanser Pro')
        self.assertEqual(str(product.price), '17.00')

    def test_webhook_rejects_invalid_signature(self):
        payload = {'id': 1}
        response = self.client.post(
            '/api/webhooks/shopify/orders/create/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_SHOPIFY_TOPIC='orders/create',
            HTTP_X_SHOPIFY_SHOP_DOMAIN='kenya-shop.myshopify.com',
            HTTP_X_SHOPIFY_HMAC_SHA256='invalid-signature',
            HTTP_X_SHOPIFY_WEBHOOK_ID='wh_invalid_sig_1',
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_webhook_rejects_unknown_shop_domain(self):
        payload = {
            'id': 123,
            'order_number': 456,
            'currency': 'USD',
            'email': 'buyer@example.com',
            'customer': {'first_name': 'Unknown', 'last_name': 'Shop', 'id': 1},
            'subtotal_price': '10.00',
            'total_tax': '0.00',
            'total_discounts': '0.00',
            'total_price': '10.00',
            'shipping_address': {'address1': 'a', 'city': 'b', 'country': 'Kenya', 'country_code': 'KE'},
            'billing_address': {'address1': 'a', 'city': 'b', 'country': 'Kenya'},
            'line_items': [{'id': 1, 'sku': 'SKU-1', 'title': 'Item', 'quantity': 1, 'price': '10.00'}],
        }

        response = self._signed_post(
            '/api/webhooks/shopify/orders/create/',
            payload,
            topic='orders/create',
            shop_domain='unknown-store.myshopify.com',
            webhook_id='wh_unknown_shop_1',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class QuickBooksOAuthTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='oauth-test@example.com',
            password='secret123',
            role=UserRole.OWNER,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.warehouse = Warehouse.objects.create(
            name='OAuth Warehouse',
            location='Nairobi',
            address='Nairobi, Kenya',
            capacity=10000,
            manager=self.user,
        )
        self.integration = Integration.objects.create(
            name='Test QuickBooks',
            type='QUICKBOOKS',
            market='Kenya',
            status='ACTIVE',
            warehouse=self.warehouse,
        )
        self.creds = QuickBooksCredentials.objects.create(
            integration=self.integration,
            realm_id='realm-test',
            client_id='client-test',
            client_key='secret-test',
        )

    @patch('integrations.views.requests.post')
    def test_quickbooks_oauth_callback_exchanges_code(self, mock_post):
        """OAuth callback should exchange code for tokens and save them."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'access_token': 'new-access-token',
            'refresh_token': 'new-refresh-token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_resp

        # Set the CSRF state nonce that must match
        csrf_token = 'test-csrf-nonce'
        self.creds.oauth_state = csrf_token
        self.creds.save(update_fields=['oauth_state'])

        # Use a fresh APIClient without auth (callback is AllowAny)
        anon_client = APIClient()
        response = anon_client.get(
            '/api/integrations/quickbooks/callback/',
            {
                'code': 'auth-code-123',
                'state': f'{self.integration.id}:{csrf_token}',
                'realmId': 'realm-updated',
            },
        )

        # Should return HTML page with postMessage script
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'quickbooks_connected', response.content)

        self.creds.refresh_from_db()
        self.assertEqual(self.creds.access_token, 'new-access-token')
        self.assertEqual(self.creds.refresh_token, 'new-refresh-token')
        self.assertEqual(self.creds.realm_id, 'realm-updated')
        self.assertIsNotNone(self.creds.token_expiry)
        self.assertEqual(self.creds.oauth_state, '')  # consumed

    def test_quickbooks_oauth_callback_rejects_missing_code(self):
        anon_client = APIClient()
        response = anon_client.get(
            '/api/integrations/quickbooks/callback/',
            {'state': f'{self.integration.id}:some-token'},
        )
        self.assertEqual(response.status_code, 400)

    def test_quickbooks_oauth_callback_rejects_bad_csrf_token(self):
        """Callback should reject if CSRF state token doesn't match."""
        self.creds.oauth_state = 'correct-nonce'
        self.creds.save(update_fields=['oauth_state'])

        anon_client = APIClient()
        response = anon_client.get(
            '/api/integrations/quickbooks/callback/',
            {
                'code': 'auth-code-123',
                'state': f'{self.integration.id}:wrong-nonce',
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_quickbooks_auth_url_action_returns_url(self):
        response = self.client.post(
            f'/api/integrations/{self.integration.id}/quickbooks-auth-url/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('auth_url', response.data)
        self.assertIn('client-test', response.data['auth_url'])
        self.assertIn('com.intuit.quickbooks.accounting', response.data['auth_url'])


class QuickBooksTokenRefreshTests(APITestCase):
    @patch('integrations.services.requests.post')
    def test_token_refresh_on_expired_token(self, mock_post):
        """_ensure_quickbooks_token should refresh when token is expired."""
        from integrations.services import _ensure_quickbooks_token
        from django.utils import timezone
        from datetime import timedelta

        integration = Integration.objects.create(
            name='Refresh Test', type='QUICKBOOKS', market='Kenya', status='ACTIVE',
        )
        creds = QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client-id',
            client_key='client-secret',
            access_token='old-access-token',
            refresh_token='valid-refresh-token',
            token_expiry=timezone.now() - timedelta(hours=1),  # expired
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            'access_token': 'fresh-access-token',
            'refresh_token': 'fresh-refresh-token',
            'expires_in': 3600,
        }
        mock_post.return_value = mock_resp

        result = _ensure_quickbooks_token(creds)

        self.assertEqual(result, 'fresh-access-token')
        creds.refresh_from_db()
        self.assertEqual(creds.access_token, 'fresh-access-token')
        self.assertEqual(creds.refresh_token, 'fresh-refresh-token')

    def test_missing_refresh_token_raises_error(self):
        from integrations.services import _ensure_quickbooks_token

        integration = Integration.objects.create(
            name='No Refresh', type='QUICKBOOKS', market='Kenya', status='ACTIVE',
        )
        creds = QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client-id',
            client_key='client-secret',
            access_token='',
            refresh_token='',
        )

        with self.assertRaisesMessage(ValueError, 'OAuth tokens are missing'):
            _ensure_quickbooks_token(creds)


class QuickBooksDuplicateInvoiceGuardTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='dup-guard@example.com',
            password='secret123',
            role=UserRole.OWNER,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.warehouse = Warehouse.objects.create(
            name='Dup Guard Warehouse',
            location='Nairobi',
            address='Nairobi, Kenya',
            capacity=10000,
            manager=self.user,
        )
        self.market = Market.objects.get_or_create(
            name='Kenya', defaults={'code': 'KE', 'currency': 'KES'},
        )[0]
        self.integration = Integration.objects.create(
            name='Kenya QuickBooks',
            type='QUICKBOOKS',
            market='Kenya',
            status='ACTIVE',
            warehouse=self.warehouse,
        )
        QuickBooksCredentials.objects.create(
            integration=self.integration,
            realm_id='realm',
            client_id='client',
            client_key='key',
        )

    def test_push_to_quickbooks_rejects_duplicate(self):
        """Should return 409 when order already has a QB invoice ID."""
        order = Order.objects.create(
            order_number='ORD-DUP-001',
            shopify_order_id='SHOP-DUP-001',
            shopify_order_number='S-DUP-001',
            market=self.market,
            customer_name='Test',
            customer_email='dup@example.com',
            subtotal_price='100.00',
            total_amount='100.00',
            shipping_address_line1='Road 1',
            shipping_city='Nairobi',
            shipping_country='Kenya',
            owner=self.user,
            quickbooks_sales_invoice_id='EXISTING-INV-123',
        )

        response = self.client.post(
            f'/api/orders/{order.id}/push-to-quickbooks/',
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('already exists', response.data['error'])


class QuickBooksRetryTests(APITestCase):
    @patch('integrations.services.time.sleep')
    @patch('integrations.services._ensure_quickbooks_token', return_value='valid-token')
    @patch('integrations.services.requests.request')
    def test_quickbooks_retry_on_429(self, mock_request, _token_mock, _sleep_mock):
        """_quickbooks_request should retry on 429 and succeed on next attempt."""
        from integrations.services import _quickbooks_request

        integration = Integration.objects.create(
            name='Retry Test', type='QUICKBOOKS', market='Kenya', status='ACTIVE',
        )
        creds = QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client',
            client_key='key',
            access_token='token',
            refresh_token='refresh',
        )

        rate_limited_resp = MagicMock()
        rate_limited_resp.status_code = 429

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.json.return_value = {'Invoice': {'Id': 'INV-RETRY'}}

        mock_request.side_effect = [rate_limited_resp, success_resp]

        result = _quickbooks_request('POST', 'https://example.com/api', creds)

        self.assertEqual(result.status_code, 200)
        self.assertEqual(mock_request.call_count, 2)

