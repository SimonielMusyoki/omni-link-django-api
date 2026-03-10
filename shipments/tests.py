from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient, APITestCase
from rest_framework import status
from django.db import connection
from django.test.utils import CaptureQueriesContext

from products.models import Warehouse, Product, Inventory
from shipments.models import Shipment, ShipmentItem

User = get_user_model()


class ShipmentModelTest(TestCase):
    """Tests for Shipment model"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='shipment@test.com',
            password='testpass123'
        )
        self.origin = Warehouse.objects.create(
            name='Origin Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )
        self.destination = Warehouse.objects.create(
            name='Destination Warehouse',
            location='LA',
            address='987 Sunset Blvd',
            capacity=1000,
            manager=self.user,
        )

    def test_create_shipment(self):
        """Test creating a shipment"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK001',
            origin=self.origin,
            destination=self.destination,
            created_by=self.user,
        )
        self.assertEqual(shipment.tracking_number, 'TRACK001')
        self.assertEqual(shipment.status, Shipment.CREATED)


class ShipmentAPITest(APITestCase):
    """Tests for Shipment API endpoints"""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email='shipment@test.com',
            password='testpass123'
        )
        self.client.force_authenticate(user=self.user)
        self.origin = Warehouse.objects.create(
            name='Origin Warehouse',
            location='NYC',
            address='123 Main St',
            capacity=1000,
            manager=self.user,
        )
        self.destination = Warehouse.objects.create(
            name='Destination Warehouse',
            location='LA',
            address='987 Sunset Blvd',
            capacity=1000,
            manager=self.user,
        )
        self.product = Product.objects.create(
            name='Shipment Product',
            sku='SHIP-001',
            price='100.00',
            is_physical=True,
        )

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

    def _seed_shipment(self, suffix: str):
        shipment = Shipment.objects.create(
            tracking_number=f'TRACK-{suffix}',
            origin=self.origin,
            destination=self.destination,
            created_by=self.user,
        )
        ShipmentItem.objects.create(shipment=shipment, product=self.product, quantity=1)
        return shipment

    def test_create_shipment(self):
        """Test creating shipment via API"""
        response = self.client.post(
            '/api/shipments/',
            {
                'origin': self.origin.id,
                'destination': self.destination.id,
                'items': [{'product': self.product.id, 'quantity': 2}],
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn('tracking_number', response.data)
        self.assertEqual(ShipmentItem.objects.count(), 1)

    def test_update_status_to_in_transit(self):
        """Test marking shipment as in transit"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK001',
            origin=self.origin,
            destination=self.destination,
            created_by=self.user,
        )
        response = self.client.post(
            f'/api/shipments/{shipment.id}/update_status/',
            {'status': Shipment.IN_TRANSIT},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], Shipment.IN_TRANSIT)

    def test_received_shipment_updates_inventory(self):
        """Test that receiving a shipment updates the inventory"""
        shipment = Shipment.objects.create(
            tracking_number='TRACK002',
            origin=self.origin,
            destination=self.destination,
            created_by=self.user,
        )
        ShipmentItem.objects.create(
            shipment=shipment,
            product=self.product,
            quantity=5,
        )

        response = self.client.post(
            f'/api/shipments/{shipment.id}/update_status/',
            {'status': Shipment.RECEIVED},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        inventory = Inventory.objects.get(product=self.product, warehouse=self.destination)
        self.assertEqual(inventory.quantity, 5)

    def test_list_shipments_query_count(self):
        shipment = Shipment.objects.create(
            tracking_number='TRACK-LIST-001',
            origin=self.origin,
            destination=self.destination,
            created_by=self.user,
        )
        ShipmentItem.objects.create(shipment=shipment, product=self.product, quantity=1)

        response = self._assert_max_queries_for_get('/api/shipments/', max_queries=6)
        self.assertEqual(response.data['count'], 1)

    def test_list_shipments_query_count_growth_is_bounded(self):
        # Baseline: one record
        Shipment.objects.all().delete()
        self._seed_shipment('BASE-1')
        baseline_queries = self._query_count_for_get('/api/shipments/')

        # Growth scenario: many records should not trigger N+1 query growth.
        Shipment.objects.all().delete()
        for idx in range(20):
            self._seed_shipment(f'N-{idx}')
        many_queries = self._query_count_for_get('/api/shipments/')

        # Allow a small constant overhead for pagination/count differences.
        self.assertLessEqual(
            many_queries,
            baseline_queries + 2,
            msg=(
                f'N+1 regression detected for shipments list: '
                f'baseline={baseline_queries}, many={many_queries}'
            ),
        )
