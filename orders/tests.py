import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from decimal import Decimal
from django.utils import timezone

from products.models import Warehouse
from orders.models import Order, OrderItem

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
            owner=self.user
        )

    def test_create_order(self):
        """Test creating an order"""
        order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user
        )
        self.assertEqual(order.order_number, 'ORD001')
        self.assertEqual(order.status, Order.PENDING)

    def test_unique_order_number(self):
        """Test order number uniqueness"""
        Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user
        )

        with self.assertRaises(Exception):
            Order.objects.create(
                order_number='ORD001',
                customer_email='customer2@test.com',
                customer_name='Jane Doe',
                total_amount=Decimal('99.99'),
                warehouse=self.warehouse,
                owner=self.user
            )


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
            owner=self.user
        )

    def test_create_order(self):
        """Test creating order via API"""
        data = {
            'order_number': 'ORD001',
            'customer_email': 'customer@test.com',
            'customer_name': 'John Doe',
            'status': 'PENDING',
            'total_amount': '199.98',
            'warehouse': self.warehouse.id,
            'items': []
        }
        response = self.client.post('/api/orders/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['order_number'], 'ORD001')

    def test_list_orders(self):
        """Test listing orders"""
        Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user
        )
        Order.objects.create(
            order_number='ORD002',
            customer_email='customer2@test.com',
            customer_name='Jane Doe',
            total_amount=Decimal('99.99'),
            warehouse=self.warehouse,
            owner=self.user
        )

        response = self.client.get('/api/orders/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 2)

    def test_ship_order(self):
        """Test shipping an order"""
        order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user
        )

        response = self.client.post(f'/api/orders/{order.id}/ship/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'SHIPPED')
        self.assertIsNotNone(response.data['shipped_at'])

    def test_deliver_order(self):
        """Test delivering an order"""
        order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user,
            status=Order.SHIPPED,
            shipped_at=timezone.now()
        )

        response = self.client.post(f'/api/orders/{order.id}/deliver/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'DELIVERED')

    def test_cancel_order(self):
        """Test cancelling an order"""
        order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user
        )

        response = self.client.post(f'/api/orders/{order.id}/cancel/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'CANCELLED')

    def test_cannot_cancel_shipped_order(self):
        """Test that shipped orders cannot be cancelled"""
        order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=Decimal('199.98'),
            warehouse=self.warehouse,
            owner=self.user,
            status=Order.SHIPPED
        )

        response = self.client.post(f'/api/orders/{order.id}/cancel/')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

