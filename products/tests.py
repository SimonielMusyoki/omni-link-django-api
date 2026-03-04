"""
Comprehensive tests for the products / inventory system.

Covers:
  • Model creation and constraints
  • Service-layer logic (add_stock, remove_stock, transfer_stock)
  • Race-condition safety (transfer with insufficient stock)
  • Every API endpoint including custom actions
  • Filtering, pagination, and error cases
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.test import APITestCase, APIClient

from products.models import (
    Category,
    Warehouse,
    Product,
    KitItem,
    Inventory,
    InventoryTransfer,
)
from products import services

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════════════
# Helper mixin
# ═══════════════════════════════════════════════════════════════════════════
class _SetupMixin:
    """Common setUp shared by most test classes."""

    def _setup(self):
        self.user = User.objects.create_user(
            email='test@example.com', password='testpass123'
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.category = Category.objects.create(
            name='Electronics', description='Electronic goods'
        )
        self.warehouse_a = Warehouse.objects.create(
            name='Warehouse A', location='NYC',
            address='100 Main St', capacity=5000, manager=self.user,
        )
        self.warehouse_b = Warehouse.objects.create(
            name='Warehouse B', location='LA',
            address='200 Oak Ave', capacity=3000, manager=self.user,
        )
        self.product = Product.objects.create(
            name='Widget', sku='WDG-001', category=self.category,
            price=Decimal('29.99'), reorder_level=10,
        )


# ═══════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════
class CategoryModelTest(APITestCase):
    def test_create_and_str(self):
        c = Category.objects.create(name='Books')
        self.assertEqual(str(c), 'Books')

    def test_unique_name(self):
        Category.objects.create(name='Books')
        with self.assertRaises(IntegrityError):
            Category.objects.create(name='Books')


class WarehouseModelTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_str(self):
        self.assertEqual(str(self.warehouse_a), 'Warehouse A (NYC)')

    def test_total_stock_empty(self):
        self.assertEqual(self.warehouse_a.total_stock, 0)

    def test_total_stock_with_inventory(self):
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=100,
        )
        self.assertEqual(self.warehouse_a.total_stock, 100)


class ProductModelTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_str(self):
        self.assertEqual(str(self.product), 'Widget (WDG-001)')

    def test_unique_sku(self):
        with self.assertRaises(IntegrityError):
            Product.objects.create(
                name='Dupe', sku='WDG-001', price=Decimal('1.00'),
            )

    def test_total_stock_across_warehouses(self):
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_b, quantity=30,
        )
        self.assertEqual(self.product.total_stock, 80)

    def test_needs_reorder(self):
        # reorder_level=10, no stock → True
        self.assertTrue(self.product.needs_reorder)
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        self.assertFalse(self.product.needs_reorder)


class InventoryModelTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_unique_constraint(self):
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=10,
        )
        with self.assertRaises(IntegrityError):
            Inventory.objects.create(
                product=self.product, warehouse=self.warehouse_a, quantity=5,
            )

    def test_str(self):
        inv = Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=42,
        )
        self.assertIn('42', str(inv))

    def test_available_property(self):
        inv = Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a,
            quantity=100, reserved=30,
        )
        self.assertEqual(inv.available, 70)

    def test_available_never_negative(self):
        inv = Inventory(quantity=5, reserved=99)
        self.assertEqual(inv.available, 0)

    def test_prevents_negative_quantity_at_db(self):
        """PositiveIntegerField rejects negative values at the DB level."""
        inv = Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=0,
        )
        inv.quantity = -1
        with self.assertRaises(Exception):
            inv.save()


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE LAYER TESTS
# ═══════════════════════════════════════════════════════════════════════════
class AddStockServiceTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_add_stock_creates_inventory(self):
        inv = services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        self.assertEqual(inv.quantity, 50)

    def test_add_stock_increments(self):
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        inv = services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=30,
        )
        self.assertEqual(inv.quantity, 80)

    def test_add_stock_zero_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.add_stock(
                product=self.product, warehouse=self.warehouse_a, quantity=0,
            )

    def test_add_stock_negative_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.add_stock(
                product=self.product, warehouse=self.warehouse_a, quantity=-5,
            )


class RemoveStockServiceTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=100,
        )

    def test_remove_stock(self):
        inv = services.remove_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=40,
        )
        self.assertEqual(inv.quantity, 60)

    def test_remove_more_than_available_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.remove_stock(
                product=self.product, warehouse=self.warehouse_a, quantity=200,
            )

    def test_remove_from_nonexistent_inventory_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.remove_stock(
                product=self.product, warehouse=self.warehouse_b, quantity=1,
            )

    def test_remove_zero_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.remove_stock(
                product=self.product, warehouse=self.warehouse_a, quantity=0,
            )


class TransferStockServiceTest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=100,
        )

    def test_transfer_stock(self):
        transfer = services.transfer_stock(
            product=self.product,
            from_warehouse=self.warehouse_a,
            to_warehouse=self.warehouse_b,
            quantity=40,
            user=self.user,
        )
        self.assertEqual(transfer.status, InventoryTransfer.Status.COMPLETED)
        self.assertIsNotNone(transfer.completed_at)

        src = Inventory.objects.get(
            product=self.product, warehouse=self.warehouse_a,
        )
        dst = Inventory.objects.get(
            product=self.product, warehouse=self.warehouse_b,
        )
        self.assertEqual(src.quantity, 60)
        self.assertEqual(dst.quantity, 40)

    def test_transfer_insufficient_stock_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.transfer_stock(
                product=self.product,
                from_warehouse=self.warehouse_a,
                to_warehouse=self.warehouse_b,
                quantity=999,
                user=self.user,
            )

    def test_transfer_same_warehouse_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.transfer_stock(
                product=self.product,
                from_warehouse=self.warehouse_a,
                to_warehouse=self.warehouse_a,
                quantity=10,
                user=self.user,
            )

    def test_transfer_creates_destination_inventory(self):
        """Destination row is auto-created when it doesn't exist."""
        self.assertFalse(
            Inventory.objects.filter(
                product=self.product, warehouse=self.warehouse_b,
            ).exists()
        )
        services.transfer_stock(
            product=self.product,
            from_warehouse=self.warehouse_a,
            to_warehouse=self.warehouse_b,
            quantity=10,
            user=self.user,
        )
        self.assertTrue(
            Inventory.objects.filter(
                product=self.product, warehouse=self.warehouse_b,
            ).exists()
        )


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – CATEGORY
# ═══════════════════════════════════════════════════════════════════════════
class CategoryAPITest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_list(self):
        r = self.client.get('/api/categories/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 1)

    def test_create(self):
        r = self.client.post('/api/categories/', {'name': 'Furniture'})
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['name'], 'Furniture')

    def test_retrieve(self):
        r = self.client.get(f'/api/categories/{self.category.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['name'], 'Electronics')

    def test_update(self):
        r = self.client.patch(
            f'/api/categories/{self.category.pk}/',
            {'description': 'Updated'},
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['description'], 'Updated')

    def test_delete(self):
        r = self.client.delete(f'/api/categories/{self.category.pk}/')
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())

    def test_unauthenticated(self):
        self.client.force_authenticate(user=None)
        r = self.client.get('/api/categories/')
        self.assertEqual(r.status_code, status.HTTP_401_UNAUTHORIZED)


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – WAREHOUSE
# ═══════════════════════════════════════════════════════════════════════════
class WarehouseAPITest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_list(self):
        r = self.client.get('/api/warehouses/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 2)

    def test_create_sets_manager(self):
        r = self.client.post('/api/warehouses/', {
            'name': 'Warehouse C', 'location': 'CHI',
            'address': '300 Elm St', 'capacity': 1000,
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['manager'], self.user.pk)

    def test_retrieve(self):
        r = self.client.get(f'/api/warehouses/{self.warehouse_a.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['name'], 'Warehouse A')

    def test_update(self):
        r = self.client.patch(
            f'/api/warehouses/{self.warehouse_a.pk}/',
            {'capacity': 9999},
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['capacity'], 9999)

    def test_delete(self):
        r = self.client.delete(f'/api/warehouses/{self.warehouse_b.pk}/')
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)

    # -- custom actions ----

    def test_inventory_action_empty(self):
        r = self.client.get(f'/api/warehouses/{self.warehouse_a.pk}/inventory/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 0)

    def test_inventory_action_with_data(self):
        Inventory.objects.create(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        r = self.client.get(f'/api/warehouses/{self.warehouse_a.pk}/inventory/')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['quantity'], 50)

    def test_add_stock_action(self):
        r = self.client.post(
            f'/api/warehouses/{self.warehouse_a.pk}/add-stock/',
            {'product': self.product.pk, 'quantity': 75},
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['quantity'], 75)

    def test_add_stock_increments(self):
        self.client.post(
            f'/api/warehouses/{self.warehouse_a.pk}/add-stock/',
            {'product': self.product.pk, 'quantity': 50},
        )
        r = self.client.post(
            f'/api/warehouses/{self.warehouse_a.pk}/add-stock/',
            {'product': self.product.pk, 'quantity': 25},
        )
        self.assertEqual(r.data['quantity'], 75)

    def test_remove_stock_action(self):
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=100,
        )
        r = self.client.post(
            f'/api/warehouses/{self.warehouse_a.pk}/remove-stock/',
            {'product': self.product.pk, 'quantity': 30},
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['quantity'], 70)

    def test_remove_stock_insufficient_returns_400(self):
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=10,
        )
        r = self.client.post(
            f'/api/warehouses/{self.warehouse_a.pk}/remove-stock/',
            {'product': self.product.pk, 'quantity': 99},
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stats_action(self):
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=200,
        )
        r = self.client.get(f'/api/warehouses/{self.warehouse_a.pk}/stats/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['total_stock'], 200)
        self.assertEqual(r.data['product_count'], 1)
        self.assertIn('total_value', r.data)
        self.assertIn('utilization_pct', r.data)


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – PRODUCT
# ═══════════════════════════════════════════════════════════════════════════
class ProductAPITest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()

    def test_list(self):
        r = self.client.get('/api/products/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 1)

    def test_create(self):
        r = self.client.post('/api/products/', {
            'name': 'Gadget', 'sku': 'GDG-001',
            'price': '49.99', 'category': self.category.pk,
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['sku'], 'GDG-001')

    def test_create_with_image_url(self):
        r = self.client.post('/api/products/', {
            'name': 'Gizmo', 'sku': 'GIZ-001', 'price': '19.99',
            'image_url': 'https://example.com/gizmo.jpg',
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['image_url'], 'https://example.com/gizmo.jpg')

    def test_retrieve(self):
        r = self.client.get(f'/api/products/{self.product.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('total_stock', r.data)
        self.assertIn('needs_reorder', r.data)

    def test_update(self):
        r = self.client.patch(
            f'/api/products/{self.product.pk}/',
            {'price': '39.99'},
        )
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['price'], '39.99')

    def test_delete(self):
        r = self.client.delete(f'/api/products/{self.product.pk}/')
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)

    def test_filter_by_category(self):
        r = self.client.get(f'/api/products/?category={self.category.pk}')
        self.assertEqual(r.data['count'], 1)

    def test_search_by_sku(self):
        r = self.client.get('/api/products/?search=WDG')
        self.assertEqual(r.data['count'], 1)

    def test_inventory_action(self):
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=50,
        )
        services.add_stock(
            product=self.product, warehouse=self.warehouse_b, quantity=30,
        )
        r = self.client.get(f'/api/products/{self.product.pk}/inventory/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 2)


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – INVENTORY (read-only)
# ═══════════════════════════════════════════════════════════════════════════
class InventoryAPITest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=100,
        )
        services.add_stock(
            product=self.product, warehouse=self.warehouse_b, quantity=60,
        )

    def test_list(self):
        r = self.client.get('/api/inventory/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 2)

    def test_retrieve(self):
        inv = Inventory.objects.first()
        r = self.client.get(f'/api/inventory/{inv.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('available', r.data)

    def test_filter_by_warehouse(self):
        r = self.client.get(f'/api/inventory/?warehouse={self.warehouse_a.pk}')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['quantity'], 100)

    def test_filter_by_product(self):
        r = self.client.get(f'/api/inventory/?product={self.product.pk}')
        self.assertEqual(r.data['count'], 2)

    def test_summary(self):
        r = self.client.get('/api/inventory/summary/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 1)
        row = r.data['results'][0]
        self.assertEqual(row['total_quantity'], 160)
        self.assertEqual(row['warehouse_count'], 2)
        self.assertFalse(row['needs_reorder'])

    def test_cannot_create_via_api(self):
        """Inventory is read-only through the InventoryViewSet."""
        r = self.client.post('/api/inventory/', {
            'product': self.product.pk,
            'warehouse': self.warehouse_a.pk,
            'quantity': 10,
        })
        self.assertEqual(r.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – TRANSFERS
# ═══════════════════════════════════════════════════════════════════════════
class TransferAPITest(APITestCase, _SetupMixin):
    def setUp(self):
        self._setup()
        services.add_stock(
            product=self.product, warehouse=self.warehouse_a, quantity=200,
        )

    def test_create_transfer(self):
        r = self.client.post('/api/transfers/', {
            'product': self.product.pk,
            'from_warehouse': self.warehouse_a.pk,
            'to_warehouse': self.warehouse_b.pk,
            'quantity': 80,
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['status'], 'COMPLETED')
        self.assertEqual(r.data['quantity'], 80)

        # verify inventory was actually moved
        src = Inventory.objects.get(
            product=self.product, warehouse=self.warehouse_a,
        )
        dst = Inventory.objects.get(
            product=self.product, warehouse=self.warehouse_b,
        )
        self.assertEqual(src.quantity, 120)
        self.assertEqual(dst.quantity, 80)

    def test_transfer_insufficient_stock_returns_400(self):
        r = self.client.post('/api/transfers/', {
            'product': self.product.pk,
            'from_warehouse': self.warehouse_a.pk,
            'to_warehouse': self.warehouse_b.pk,
            'quantity': 9999,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_transfer_same_warehouse_returns_400(self):
        r = self.client.post('/api/transfers/', {
            'product': self.product.pk,
            'from_warehouse': self.warehouse_a.pk,
            'to_warehouse': self.warehouse_a.pk,
            'quantity': 10,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_transfers(self):
        services.transfer_stock(
            product=self.product,
            from_warehouse=self.warehouse_a,
            to_warehouse=self.warehouse_b,
            quantity=50, user=self.user,
        )
        r = self.client.get('/api/transfers/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 1)

    def test_retrieve_transfer(self):
        t = services.transfer_stock(
            product=self.product,
            from_warehouse=self.warehouse_a,
            to_warehouse=self.warehouse_b,
            quantity=25, user=self.user,
        )
        r = self.client.get(f'/api/transfers/{t.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['quantity'], 25)

    def test_filter_transfers_by_status(self):
        services.transfer_stock(
            product=self.product,
            from_warehouse=self.warehouse_a,
            to_warehouse=self.warehouse_b,
            quantity=10, user=self.user,
        )
        r = self.client.get('/api/transfers/?status=COMPLETED')
        self.assertEqual(r.data['count'], 1)
        r = self.client.get('/api/transfers/?status=PENDING')
        self.assertEqual(r.data['count'], 0)

    def test_transfer_with_notes(self):
        r = self.client.post('/api/transfers/', {
            'product': self.product.pk,
            'from_warehouse': self.warehouse_a.pk,
            'to_warehouse': self.warehouse_b.pk,
            'quantity': 5,
            'notes': 'Seasonal rebalancing',
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['notes'], 'Seasonal rebalancing')


# ═══════════════════════════════════════════════════════════════════════════
# Helper mixin for kit tests
# ═══════════════════════════════════════════════════════════════════════════
class _KitSetupMixin(_SetupMixin):
    """Extends _SetupMixin with a kit product and two components."""

    def _kit_setup(self):
        self._setup()
        self.component_a = Product.objects.create(
            name='Screw', sku='SCR-001', price=Decimal('0.50'),
        )
        self.component_b = Product.objects.create(
            name='Bolt', sku='BLT-001', price=Decimal('1.00'),
        )
        self.kit = Product.objects.create(
            name='Starter Pack', sku='KIT-001',
            price=Decimal('9.99'), is_kit=True,
        )
        # Kit = 3× Screw + 2× Bolt
        self.kit_item_a = KitItem.objects.create(
            kit=self.kit, component=self.component_a, quantity=3,
        )
        self.kit_item_b = KitItem.objects.create(
            kit=self.kit, component=self.component_b, quantity=2,
        )


# ═══════════════════════════════════════════════════════════════════════════
# MODEL TESTS – KitItem
# ═══════════════════════════════════════════════════════════════════════════
class KitItemModelTest(APITestCase, _KitSetupMixin):
    def setUp(self):
        self._kit_setup()

    def test_str(self):
        self.assertIn('3×', str(self.kit_item_a))
        self.assertIn('Screw', str(self.kit_item_a))

    def test_unique_constraint(self):
        """Same component cannot appear twice in one kit."""
        with self.assertRaises(IntegrityError):
            KitItem.objects.create(
                kit=self.kit, component=self.component_a, quantity=5,
            )

    def test_clean_self_reference(self):
        """A kit cannot contain itself."""
        item = KitItem(kit=self.kit, component=self.kit, quantity=1)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            item.clean()

    def test_clean_nested_kit(self):
        """A kit cannot contain another kit as a component."""
        other_kit = Product.objects.create(
            name='Super Pack', sku='KIT-002', price=Decimal('19.99'), is_kit=True,
        )
        item = KitItem(kit=other_kit, component=self.kit, quantity=1)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            item.clean()

    def test_product_is_kit_flag(self):
        self.assertTrue(self.kit.is_kit)
        self.assertFalse(self.component_a.is_kit)

    def test_kit_items_relation(self):
        self.assertEqual(self.kit.kit_items.count(), 2)

    def test_used_in_kits_relation(self):
        self.assertEqual(self.component_a.used_in_kits.count(), 1)


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE TESTS – assemble_kit
# ═══════════════════════════════════════════════════════════════════════════
class AssembleKitServiceTest(APITestCase, _KitSetupMixin):
    def setUp(self):
        self._kit_setup()
        # Stock components: 30 screws + 20 bolts in warehouse A
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=30)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=20)

    def test_assemble_kit(self):
        """Assemble 5 kits → consume 15 screws + 10 bolts, produce 5 kits."""
        kit_inv = services.assemble_kit(
            kit=self.kit, warehouse=self.warehouse_a, quantity=5,
        )
        self.assertEqual(kit_inv.quantity, 5)
        self.assertEqual(
            Inventory.objects.get(product=self.component_a, warehouse=self.warehouse_a).quantity,
            15,  # 30 - 15
        )
        self.assertEqual(
            Inventory.objects.get(product=self.component_b, warehouse=self.warehouse_a).quantity,
            10,  # 20 - 10
        )

    def test_assemble_insufficient_component(self):
        """Cannot assemble if a component is insufficient."""
        with self.assertRaises(DRFValidationError):
            services.assemble_kit(
                kit=self.kit, warehouse=self.warehouse_a, quantity=100,
            )
        # Nothing should have changed
        self.assertEqual(
            Inventory.objects.get(product=self.component_a, warehouse=self.warehouse_a).quantity,
            30,
        )

    def test_assemble_non_kit_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.assemble_kit(
                kit=self.component_a, warehouse=self.warehouse_a, quantity=1,
            )

    def test_assemble_zero_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.assemble_kit(
                kit=self.kit, warehouse=self.warehouse_a, quantity=0,
            )

    def test_assemble_no_components_rejected(self):
        """Kit with no components defined cannot be assembled."""
        empty_kit = Product.objects.create(
            name='Empty Kit', sku='KIT-EMPTY', price=Decimal('5.00'), is_kit=True,
        )
        with self.assertRaises(DRFValidationError):
            services.assemble_kit(
                kit=empty_kit, warehouse=self.warehouse_a, quantity=1,
            )

    def test_assemble_missing_component_inventory_rejected(self):
        """Component has no inventory row in the target warehouse."""
        with self.assertRaises(DRFValidationError):
            services.assemble_kit(
                kit=self.kit, warehouse=self.warehouse_b, quantity=1,
            )

    def test_assemble_increments_existing_kit_stock(self):
        """Assembling twice accumulates kit stock."""
        services.assemble_kit(kit=self.kit, warehouse=self.warehouse_a, quantity=2)
        kit_inv = services.assemble_kit(kit=self.kit, warehouse=self.warehouse_a, quantity=3)
        self.assertEqual(kit_inv.quantity, 5)


# ═══════════════════════════════════════════════════════════════════════════
# SERVICE TESTS – disassemble_kit
# ═══════════════════════════════════════════════════════════════════════════
class DisassembleKitServiceTest(APITestCase, _KitSetupMixin):
    def setUp(self):
        self._kit_setup()
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=30)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=20)
        # Assemble 5 kits first
        services.assemble_kit(kit=self.kit, warehouse=self.warehouse_a, quantity=5)
        # State: screws=15, bolts=10, kit=5

    def test_disassemble_kit(self):
        """Disassemble 2 kits → return 6 screws + 4 bolts."""
        result = services.disassemble_kit(
            kit=self.kit, warehouse=self.warehouse_a, quantity=2,
        )
        self.assertEqual(len(result), 2)
        kit_inv = Inventory.objects.get(product=self.kit, warehouse=self.warehouse_a)
        self.assertEqual(kit_inv.quantity, 3)
        self.assertEqual(
            Inventory.objects.get(product=self.component_a, warehouse=self.warehouse_a).quantity,
            21,  # 15 + 6
        )
        self.assertEqual(
            Inventory.objects.get(product=self.component_b, warehouse=self.warehouse_a).quantity,
            14,  # 10 + 4
        )

    def test_disassemble_insufficient_kit_stock(self):
        with self.assertRaises(DRFValidationError):
            services.disassemble_kit(
                kit=self.kit, warehouse=self.warehouse_a, quantity=99,
            )

    def test_disassemble_non_kit_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.disassemble_kit(
                kit=self.component_a, warehouse=self.warehouse_a, quantity=1,
            )

    def test_disassemble_no_inventory_rejected(self):
        with self.assertRaises(DRFValidationError):
            services.disassemble_kit(
                kit=self.kit, warehouse=self.warehouse_b, quantity=1,
            )


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – Product Kits
# ═══════════════════════════════════════════════════════════════════════════
class KitAPITest(APITestCase, _KitSetupMixin):
    def setUp(self):
        self._kit_setup()

    def test_product_response_includes_is_kit_and_kit_items(self):
        r = self.client.get(f'/api/products/{self.kit.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r.data['is_kit'])
        self.assertEqual(len(r.data['kit_items']), 2)

    def test_regular_product_has_empty_kit_items(self):
        r = self.client.get(f'/api/products/{self.product.pk}/')
        self.assertEqual(r.data['is_kit'], False)
        self.assertEqual(r.data['kit_items'], [])

    def test_filter_kits_only(self):
        r = self.client.get('/api/products/?is_kit=true')
        self.assertEqual(r.data['count'], 1)
        self.assertEqual(r.data['results'][0]['sku'], 'KIT-001')

    def test_filter_non_kits(self):
        r = self.client.get('/api/products/?is_kit=false')
        # product + component_a + component_b
        self.assertEqual(r.data['count'], 3)

    def test_create_kit_product(self):
        r = self.client.post('/api/products/', {
            'name': 'Mega Pack', 'sku': 'KIT-MEGA',
            'price': '49.99', 'is_kit': True,
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertTrue(r.data['is_kit'])

    def test_assemble_action(self):
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=30)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=20)

        r = self.client.post(f'/api/products/{self.kit.pk}/assemble/', {
            'warehouse': self.warehouse_a.pk, 'quantity': 5,
        })
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['quantity'], 5)
        self.assertEqual(r.data['product'], self.kit.pk)

    def test_assemble_insufficient_returns_400(self):
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=1)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=1)

        r = self.client.post(f'/api/products/{self.kit.pk}/assemble/', {
            'warehouse': self.warehouse_a.pk, 'quantity': 999,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_assemble_non_kit_returns_400(self):
        r = self.client.post(f'/api/products/{self.product.pk}/assemble/', {
            'warehouse': self.warehouse_a.pk, 'quantity': 1,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_disassemble_action(self):
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=30)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=20)
        services.assemble_kit(kit=self.kit, warehouse=self.warehouse_a, quantity=5)

        r = self.client.post(f'/api/products/{self.kit.pk}/disassemble/', {
            'warehouse': self.warehouse_a.pk, 'quantity': 2,
        })
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data), 2)  # two component inventory rows

    def test_disassemble_insufficient_returns_400(self):
        services.add_stock(product=self.component_a, warehouse=self.warehouse_a, quantity=30)
        services.add_stock(product=self.component_b, warehouse=self.warehouse_a, quantity=20)
        services.assemble_kit(kit=self.kit, warehouse=self.warehouse_a, quantity=1)

        r = self.client.post(f'/api/products/{self.kit.pk}/disassemble/', {
            'warehouse': self.warehouse_a.pk, 'quantity': 99,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)


# ═══════════════════════════════════════════════════════════════════════════
# API TESTS – KitItem CRUD
# ═══════════════════════════════════════════════════════════════════════════
class KitItemAPITest(APITestCase, _KitSetupMixin):
    def setUp(self):
        self._kit_setup()

    def test_list(self):
        r = self.client.get('/api/kit-items/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['count'], 2)

    def test_filter_by_kit(self):
        r = self.client.get(f'/api/kit-items/?kit={self.kit.pk}')
        self.assertEqual(r.data['count'], 2)

    def test_create_kit_item(self):
        new_comp = Product.objects.create(
            name='Nut', sku='NUT-001', price=Decimal('0.25'),
        )
        r = self.client.post('/api/kit-items/', {
            'kit': self.kit.pk, 'component': new_comp.pk, 'quantity': 4,
        })
        self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        self.assertEqual(r.data['quantity'], 4)

    def test_create_self_reference_rejected(self):
        r = self.client.post('/api/kit-items/', {
            'kit': self.kit.pk, 'component': self.kit.pk, 'quantity': 1,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_nested_kit_rejected(self):
        other_kit = Product.objects.create(
            name='Other Kit', sku='KIT-OTHER', price=Decimal('9.99'), is_kit=True,
        )
        r = self.client.post('/api/kit-items/', {
            'kit': self.kit.pk, 'component': other_kit.pk, 'quantity': 1,
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_quantity(self):
        r = self.client.patch(f'/api/kit-items/{self.kit_item_a.pk}/', {
            'quantity': 10,
        })
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['quantity'], 10)

    def test_delete(self):
        r = self.client.delete(f'/api/kit-items/{self.kit_item_a.pk}/')
        self.assertEqual(r.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self.kit.kit_items.count(), 1)

    def test_retrieve_includes_component_details(self):
        r = self.client.get(f'/api/kit-items/{self.kit_item_a.pk}/')
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data['component_name'], 'Screw')
        self.assertEqual(r.data['component_sku'], 'SCR-001')

