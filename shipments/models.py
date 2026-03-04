from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator

User = get_user_model()


class Shipment(models.Model):
    """Shipment model to track shipments"""

    PROCESSING = 'PROCESSING'
    IN_TRANSIT = 'IN_TRANSIT'
    DELIVERED = 'DELIVERED'
    RETURNED = 'RETURNED'
    FAILED = 'FAILED'

    STATUS_CHOICES = [
        (PROCESSING, 'Processing'),
        (IN_TRANSIT, 'In Transit'),
        (DELIVERED, 'Delivered'),
        (RETURNED, 'Returned'),
        (FAILED, 'Failed'),
    ]

    id = models.AutoField(primary_key=True)
    tracking_number = models.CharField(max_length=100, unique=True)
    order = models.ForeignKey('orders.Order', on_delete=models.CASCADE, related_name='shipments')
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.SET_NULL, null=True, related_name='shipments')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PROCESSING)

    # Shipping address
    recipient_name = models.CharField(max_length=255)
    recipient_email = models.EmailField()
    shipping_address = models.TextField()
    shipping_city = models.CharField(max_length=100)
    shipping_state = models.CharField(max_length=100)
    shipping_zip = models.CharField(max_length=20)
    shipping_country = models.CharField(max_length=100)

    # Logistics
    carrier = models.CharField(max_length=100, blank=True)
    estimated_delivery = models.DateTimeField(null=True, blank=True)
    actual_delivery = models.DateTimeField(null=True, blank=True)
    weight = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])

    # Metadata
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shipments')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tracking_number']),
            models.Index(fields=['order']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['owner', '-created_at']),
        ]

    def __str__(self):
        return f"Shipment {self.tracking_number}"

