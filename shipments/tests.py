import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.utils import timezone

from products.models import Warehouse
from shipments.models import Shipment
from orders.models import Order

User = get_user_model()


class ShipmentModelTest(TestCase):
    """Tests for Shipment model"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='shipment@test.com',
            password='testpass123'
        )
        self.warehouse = Warehouse.objects.create(
            name='Test Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            owner=self.user
        )
        self.order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=100.00,
            warehouse=self.warehouse,
            owner=self.user
        )

    def test_create_shipment(self):
        """Test creating a shipment"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK001',
            order=self.order,
            warehouse=self.warehouse,
            recipient_name='John Doe',
            recipient_email='customer@test.com',
            shipping_address='123 Main St',
            shipping_city='NYC',
            shipping_state='NY',
            shipping_zip='10001',
            shipping_country='USA',
            owner=self.user
        )
        self.assertEqual(shipment.tracking_number, 'TRACK001')
        self.assertEqual(shipment.status, Shipment.PROCESSING)


class ShipmentAPITest(APITestCase):
    """Tests for Shipment API endpoints"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='shipment@test.com',
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
        self.order = Order.objects.create(
            order_number='ORD001',
            customer_email='customer@test.com',
            customer_name='John Doe',
            total_amount=100.00,
            warehouse=self.warehouse,
            owner=self.user
        )

    def test_create_shipment(self):
        """Test creating shipment via API"""
        data = {
            'tracking_number': 'TRACK001',
            'order': self.order.id,
            'warehouse': self.warehouse.id,
            'recipient_name': 'John Doe',
            'recipient_email': 'customer@test.com',
            'shipping_address': '123 Main St',
            'shipping_city': 'NYC',
            'shipping_state': 'NY',
            'shipping_zip': '10001',
            'shipping_country': 'USA'
        }
        response = self.client.post('/api/shipments/', data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['tracking_number'], 'TRACK001')

    def test_mark_in_transit(self):
        """Test marking shipment as in transit"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK001',
            order=self.order,
            warehouse=self.warehouse,
            recipient_name='John Doe',
            recipient_email='customer@test.com',
            shipping_address='123 Main St',
            shipping_city='NYC',
            shipping_state='NY',
            shipping_zip='10001',
            shipping_country='USA',
            owner=self.user
        )

        response = self.client.post(f'/api/shipments/{shipment.id}/mark_in_transit/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'IN_TRANSIT')

    def test_mark_delivered(self):
        """Test marking shipment as delivered"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK001',
            order=self.order,
            warehouse=self.warehouse,
            recipient_name='John Doe',
            recipient_email='customer@test.com',
            shipping_address='123 Main St',
            shipping_city='NYC',
            shipping_state='NY',
            shipping_zip='10001',
            shipping_country='USA',
            owner=self.user,
            status=Shipment.IN_TRANSIT
        )

        response = self.client.post(f'/api/shipments/{shipment.id}/mark_delivered/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'DELIVERED')
        self.assertIsNotNone(response.data['actual_delivery'])

