from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from decimal import Decimal
from django.utils import timezone
from django.db import connection
from django.test.utils import CaptureQueriesContext

from products.models import Warehouse, Market
from orders.models import Order

User = get_user_model()


class OrderModelTest(TestCase):
    """Tests for Order model"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='order@test.com',
            password='testpass123'
        )
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )
        self.market, _ = Market.objects.get_or_create(
            name='Kenya',
            defaults={'code': 'KE', 'currency': 'KES'},
        )

    def _create_order(self, **overrides):
        payload = {
            'order_number': 'ORD001',
            'shopify_order_id': 'SHP-001',
            'shopify_order_number': '1001',
            'market': self.market,
            'customer_email': 'customer@test.com',
            'customer_name': 'John Doe',
            'subtotal_price': Decimal('199.98'),
            'total_tax': Decimal('0.00'),
            'shipping_price': Decimal('0.00'),
            'discount_amount': Decimal('0.00'),
            'total_amount': Decimal('199.98'),
            'shipping_address_line1': '123 Main St',
            'shipping_city': 'Nairobi',
            'shipping_country': 'Kenya',
            'warehouse': self.warehouse,
            'owner': self.user,
        }
        payload.update(overrides)
        return Order.objects.create(**payload)

    def test_create_order(self):
        """Test creating an order"""
        order = self._create_order()
        self.assertEqual(order.order_number, 'ORD001')
        self.assertEqual(order.status, Order.PENDING)
        self.assertEqual(order.currency, 'KES')

    def test_unique_order_number(self):
        """Test order number uniqueness"""
        self._create_order()
        with self.assertRaises(Exception):
            self._create_order(shopify_order_id='SHP-002', order_number='ORD001')


class OrderAPITest(APITestCase):
    """Tests for Order API endpoints"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='order@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )
        self.market, _ = Market.objects.get_or_create(
            name='Kenya',
            defaults={'code': 'KE', 'currency': 'KES'},
        )

    def _create_order(self, idx=1, **overrides):
        payload = {
            'order_number': f'ORD{idx:03d}',
            'shopify_order_id': f'SHP-{idx:03d}',
            'shopify_order_number': str(1000 + idx),
            'market': self.market,
            'customer_email': f'customer{idx}@test.com',
            'customer_name': 'John Doe',
            'subtotal_price': Decimal('199.98'),
            'total_tax': Decimal('0.00'),
            'shipping_price': Decimal('0.00'),
            'discount_amount': Decimal('0.00'),
            'total_amount': Decimal('199.98'),
            'shipping_address_line1': '123 Main St',
            'shipping_city': 'Nairobi',
            'shipping_country': 'Kenya',
            'warehouse': self.warehouse,
            'owner': self.user,
        }
        payload.update(overrides)
        return Order.objects.create(**payload)

    def _assert_max_queries_for_get(self, url: str, max_queries: int):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(
            len(queries),
            max_queries,
            msg=f"Expected <= {max_queries} queries for {url}, got {len(queries)}",
        )
        return response

    def _query_count_for_get(self, url: str) -> int:
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(queries)

    def test_create_order(self):
        """Test retrieving a seeded order"""
        order = self._create_order(idx=1)
        response = self.client.get(f'/api/orders/{order.id}/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['order_number'], order.order_number)

    def test_list_orders(self):
        """Test listing orders"""
        self._create_order(idx=1)
        self._create_order(idx=2)

        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_authenticated_user_can_view_other_users_orders(self):
        other_user = User.objects.create_user(email='other-order@test.com', password='testpass123')
        Order.objects.create(
            order_number='ORD999',
            shopify_order_id='SHP-999',
            shopify_order_number='1999',
            market=self.market,
            customer_email='other-customer@test.com',
            customer_name='Other Customer',
            subtotal_price=Decimal('50.00'),
            total_tax=Decimal('0.00'),
            shipping_price=Decimal('0.00'),
            discount_amount=Decimal('0.00'),
            total_amount=Decimal('50.00'),
            shipping_address_line1='123 Other St',
            shipping_city='Nairobi',
            shipping_country='Kenya',
            warehouse=self.warehouse,
            owner=other_user,
        )

        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_list_orders_query_count(self):
        self._create_order(idx=1)
        self._create_order(idx=2)

        response = self._assert_max_queries_for_get('/api/orders/', max_queries=7)
        self.assertEqual(response.data['count'], 2)

    def test_ship_order(self):
        """Test shipping an order"""
        order = self._create_order(idx=3)
        response = self.client.post(f'/api/orders/{order.id}/ship/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Order.SHIPPED)

    def test_deliver_order(self):
        """Test delivering an order"""
        order = self._create_order(
            idx=4,
            status=Order.SHIPPED,
            shipped_at=timezone.now(),
        )
        response = self.client.post(f'/api/orders/{order.id}/deliver/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Order.DELIVERED)

    def test_cancel_order(self):
        """Test cancelling an order"""
        order = self._create_order(idx=5)
        response = self.client.post(f'/api/orders/{order.id}/cancel/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Order.CANCELLED)

    def test_cannot_cancel_shipped_order(self):
        """Test that shipped orders cannot be cancelled"""
        order = self._create_order(idx=6, status=Order.SHIPPED)
        response = self.client.post(f'/api/orders/{order.id}/cancel/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_orders_query_count_growth_is_bounded(self):
        Order.objects.all().delete()
        self._create_order(idx=1)
        baseline_queries = self._query_count_for_get('/api/orders/')

        Order.objects.all().delete()
        for idx in range(1, 21):
            self._create_order(idx=idx)
        many_queries = self._query_count_for_get('/api/orders/')

        self.assertLessEqual(
            many_queries,
            baseline_queries + 2,
            msg=(
                f'N+1 regression detected for orders list: '
                f'baseline={baseline_queries}, many={many_queries}'
            ),
        )
