from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from products.models import Category, Product, Warehouse

User = get_user_model()


class VirtualProductTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='virtual@example.com',
            password='testpass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.category = Category.objects.create(name='Services')
        self.warehouse = Warehouse.objects.create(
            name='Main Warehouse',
            location='Nairobi',
            address='123 Warehouse Rd',
            capacity=1000,
            manager=self.user,
        )

    def test_product_defaults_to_physical(self):
        product = Product.objects.create(
            name='Physical Item',
            sku='PHY-001',
            category=self.category,
            price=Decimal('10.00'),
        )
        self.assertTrue(product.is_physical)

    def test_virtual_product_inventory_endpoint_returns_empty(self):
        product = Product.objects.create(
            name='Consultation',
            sku='VIR-001',
            category=self.category,
            price=Decimal('50.00'),
            is_physical=False,
            reorder_level=0,
        )

        response = self.client.get(f'/api/products/{product.id}/inventory/')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 0)
        self.assertEqual(response.data['results'], [])

    def test_add_stock_rejected_for_virtual_product(self):
        product = Product.objects.create(
            name='Virtual Course',
            sku='VIR-002',
            category=self.category,
            price=Decimal('99.00'),
            is_physical=False,
            reorder_level=0,
        )

        response = self.client.post(
            f'/api/warehouses/{self.warehouse.id}/add-stock/',
            {'product': product.id, 'quantity': 5},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Virtual products do not track inventory.', str(response.data))

