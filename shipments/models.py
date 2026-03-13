from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from decimal import Decimal

User = get_user_model()


class Shipment(models.Model):
    """Shipment model to track shipments between warehouses"""

    CREATED = 'CREATED'
    IN_TRANSIT = 'IN_TRANSIT'
    AT_PORT = 'AT_PORT'
    CUSTOMS_CLEARANCE = 'CUSTOMS_CLEARANCE'
    OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY'
    RECEIVED = 'RECEIVED'

    STATUS_CHOICES = [
        (CREATED, 'Created'),
        (IN_TRANSIT, 'In Transit'),
        (AT_PORT, 'At Port'),
        (CUSTOMS_CLEARANCE, 'Customs Clearance'),
        (OUT_FOR_DELIVERY, 'Out for Delivery'),
        (RECEIVED, 'Received'),
    ]

    id = models.AutoField(primary_key=True)
    tracking_number = models.CharField(max_length=100, unique=True)

    # Origin and Destination
    origin = models.ForeignKey(
        'products.Warehouse',
        on_delete=models.PROTECT,
        related_name='shipments_from',
        help_text='Warehouse where shipment originates'
    )
    destination = models.ForeignKey(
        'products.Warehouse',
        on_delete=models.PROTECT,
        related_name='shipments_to',
        help_text='Warehouse where shipment is delivered'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=CREATED)

    # Logistics
    carrier = models.CharField(max_length=100, blank=True)
    estimated_delivery = models.DateTimeField(null=True, blank=True)
    actual_delivery = models.DateTimeField(null=True, blank=True)
    weight = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal('0'))],
        help_text='Weight in kg'
    )
    notes = models.TextField(blank=True, help_text='Additional notes about the shipment')

    # Metadata
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shipments_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tracking_number']),
            models.Index(fields=['origin', '-created_at']),
            models.Index(fields=['destination', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['created_by', '-created_at']),
        ]

    def __str__(self):
        return f"Shipment {self.tracking_number}"


class ShipmentItem(models.Model):
    """Individual product item in a shipment"""

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE, related_name='shipment_items')
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        unique_together = ['shipment', 'product']
        indexes = [
            models.Index(fields=['shipment', 'product']),
        ]

    def __str__(self):
        return f"{self.product.name} x{self.quantity} in Shipment {self.shipment.tracking_number}"


