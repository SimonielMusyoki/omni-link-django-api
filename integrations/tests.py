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
from orders.models import Order
from products.models import Product, Warehouse

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
        QuickBooksCredentials.objects.create(
            integration=integration,
            realm_id='realm',
            client_id='client',
            client_key='key',
        )

        response = self.client.post(
            f'/api/integrations/{integration.id}/test-connection/'
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['success'])

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
