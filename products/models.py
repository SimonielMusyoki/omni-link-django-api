"""
Products app models.

Architecture Decision:
- Product is decoupled from Warehouse (no FK from Product → Warehouse).
- The Inventory model is the explicit many-to-many intermediate table
  that tracks "how many units of Product X are in Warehouse Y".
- InventoryTransfer records atomic stock movements between warehouses.
- A DB-level CHECK constraint prevents negative inventory.
- select_for_update() is used in business logic to prevent race conditions.
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from decimal import Decimal

User = get_user_model()


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
class Market(models.Model):
    """Market/Country configuration for multi-region operations"""

    name = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        help_text='Market/Country name (e.g., Kenya, Nigeria, Ghana)'
    )
    code = models.CharField(
        max_length=10,
        unique=True,
        db_index=True,
        help_text='Market code (e.g., KE, NG, GH)'
    )
    currency = models.CharField(
        max_length=3,
        default='USD',
        help_text='Default currency code (e.g., KES, NGN, GHS)'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this market is currently active'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['code']),
            models.Index(fields=['is_active', 'name']),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
class Category(models.Model):
    """Organises products into groups (e.g. Electronics, Furniture)."""

    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
class Warehouse(models.Model):
    """Physical warehouse / storage location."""

    name = models.CharField(max_length=255, unique=True)
    location = models.CharField(max_length=255)
    address = models.TextField()
    capacity = models.PositiveIntegerField(
        help_text='Maximum number of units the warehouse can hold.',
    )
    manager = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='managed_warehouses',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['name']),
            models.Index(fields=['manager']),
        ]

    def __str__(self):
        return f'{self.name} ({self.location})'

    @property
    def total_stock(self):
        """Sum of all inventory quantities in this warehouse."""
        return (
            self.inventory_items.aggregate(total=models.Sum('quantity'))['total']
            or 0
        )


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class Product(models.Model):
    """
    Catalogue product – warehouse-agnostic.
    Actual per-warehouse quantities live in Inventory.

    A product with ``is_bundle=True`` is a *Product Bundle* – a composite
    product whose components are defined via :model:`ProductBundle`.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    sku = models.CharField(max_length=100, unique=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
    )
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0'))],
    )
    reorder_level = models.PositiveIntegerField(
        default=10,
        help_text='Minimum total stock before a reorder alert fires.',
    )
    image_url = models.URLField(blank=True, default='')
    is_bundle = models.BooleanField(
        default=False,
        help_text='True if this product is a bundle composed of other products.',
    )
    is_physical = models.BooleanField(
        default=True,
        help_text='Physical products track warehouse inventory; virtual products do not.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __init__(self, *args, **kwargs):
        # Backward compatibility: legacy code/tests still pass is_kit.
        if 'is_kit' in kwargs and 'is_bundle' not in kwargs:
            kwargs['is_bundle'] = kwargs.pop('is_kit')
        super().__init__(*args, **kwargs)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['sku']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return f'{self.name} ({self.sku})'

    @property
    def total_stock(self):
        """Sum of quantity across every warehouse for physical products."""
        if not self.is_physical:
            return 0
        return (
            self.inventory_items.aggregate(total=models.Sum('quantity'))['total']
            or 0
        )

    @property
    def needs_reorder(self):
        if not self.is_physical:
            return False
        return self.total_stock <= self.reorder_level

    @property
    def is_kit(self):
        return self.is_bundle

    @is_kit.setter
    def is_kit(self, value):
        self.is_bundle = value

    @property
    def kit_items(self):
        return self.bundle_items

    @property
    def used_in_kits(self):
        return self.used_in_bundles


# ---------------------------------------------------------------------------
# ProductBundle  (components of a product bundle)
# ---------------------------------------------------------------------------
class ProductBundle(models.Model):
    """
    One line inside a Product Bundle's bill-of-materials.

    Example: Bundle "Starter Pack" contains 2× Widget + 1× Gadget.
    Each of those lines is a ProductBundle.
    """

    bundle = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='bundle_items',
        limit_choices_to={'is_bundle': True},
        help_text='The bundle (parent) product.',
    )
    component = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='used_in_bundles',
        help_text='The component product included in the bundle.',
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text='How many units of the component are in one unit of the bundle.',
    )

    class Meta:
        ordering = ['bundle', 'component']
        constraints = [
            models.UniqueConstraint(
                fields=['bundle', 'component'],
                name='unique_component_per_bundle',
            ),
        ]

    def __str__(self):
        return f'{self.bundle.name} → {self.quantity}× {self.component.name}'

    def clean(self):
        if self.bundle_id == self.component_id:
            raise ValidationError('A bundle cannot contain itself.')
        if self.component_id and self.component.is_bundle:
            raise ValidationError('Nested bundles are not supported. Components must be regular products.')

    @property
    def kit(self):
        return self.bundle

    @kit.setter
    def kit(self, value):
        self.bundle = value


class KitItem(models.Model):
    """Legacy alias over ProductBundle for backward compatibility."""

    kit = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='legacy_kit_items',
        db_column='bundle_id',
    )
    component = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='legacy_used_in_kits',
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    class Meta:
        managed = False
        db_table = ProductBundle._meta.db_table
        ordering = ['kit', 'component']

    def __str__(self):
        return f'{self.kit.name} -> {self.quantity}× {self.component.name}'

    def clean(self):
        if self.kit_id == self.component_id:
            raise ValidationError('A bundle cannot contain itself.')
        if self.component_id and self.component.is_bundle:
            raise ValidationError('Nested bundles are not supported. Components must be regular products.')


# ---------------------------------------------------------------------------
# Inventory  (the intermediate model)
# ---------------------------------------------------------------------------
class Inventory(models.Model):
    """
    Tracks the quantity of a single product in a single warehouse.

    Unique constraint: one row per (product, warehouse) pair.
    The PositiveIntegerField at the DB level prevents negative quantities.
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='inventory_items',
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name='inventory_items',
    )
    quantity = models.PositiveIntegerField(
        default=0,
        help_text='Units of this product currently in this warehouse.',
    )
    reserved = models.PositiveIntegerField(
        default=0,
        help_text='Units reserved for pending orders / transfers.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name_plural = 'inventory'
        constraints = [
            models.UniqueConstraint(
                fields=['product', 'warehouse'],
                name='unique_product_per_warehouse',
            ),
        ]
        indexes = [
            models.Index(fields=['product', 'warehouse']),
        ]

    def __str__(self):
        return f'{self.product.name} @ {self.warehouse.name}: {self.quantity}'

    @property
    def available(self):
        """Quantity minus reserved."""
        return max(0, self.quantity - self.reserved)

    def clean(self):
        if self.reserved > self.quantity:
            raise ValidationError(
                'Reserved stock cannot exceed total quantity.'
            )


# ---------------------------------------------------------------------------
# InventoryTransfer
# ---------------------------------------------------------------------------
class InventoryTransfer(models.Model):
    """
    Records an atomic stock movement from one warehouse to another.

    Lifecycle:
        PENDING   → the transfer request has been created
        COMPLETED → stock has been moved
        FAILED    → something went wrong, stock was NOT moved
    """

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='transfers',
    )
    from_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name='transfers_out',
    )
    to_warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.CASCADE,
        related_name='transfers_in',
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    notes = models.TextField(blank=True)
    initiated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='initiated_transfers',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['product', 'status']),
        ]

    def __str__(self):
        return (
            f'Transfer #{self.pk}: {self.product.name} '
            f'×{self.quantity} {self.from_warehouse.name} → {self.to_warehouse.name}'
        )

    def clean(self):
        if self.from_warehouse_id == self.to_warehouse_id:
            raise ValidationError(
                'Source and destination warehouse must be different.'
            )


# ---------------------------------------------------------------------------
# Integration has moved to integrations/models.py
# ---------------------------------------------------------------------------
