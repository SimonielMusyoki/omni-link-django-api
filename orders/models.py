from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator

User = get_user_model()


class Order(models.Model):
    """Order model to store customer orders"""

    PENDING = 'PENDING'
    CONFIRMED = 'CONFIRMED'
    SHIPPED = 'SHIPPED'
    DELIVERED = 'DELIVERED'
    CANCELLED = 'CANCELLED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (CONFIRMED, 'Confirmed'),
        (SHIPPED, 'Shipped'),
        (DELIVERED, 'Delivered'),
        (CANCELLED, 'Cancelled'),
    ]

    id = models.AutoField(primary_key=True)
    order_number = models.CharField(max_length=100, unique=True)
    customer_email = models.EmailField()
    customer_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.SET_NULL, null=True, related_name='orders')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_number']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['owner', '-created_at']),
            models.Index(fields=['warehouse', '-created_at']),
        ]

    def __str__(self):
        return f"Order {self.order_number}"


class OrderItem(models.Model):
    """OrderItem model to store items within an order"""

    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True, related_name='order_items')
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    total_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['product']),
        ]

    def __str__(self):
        return f"OrderItem {self.id} - {self.order.order_number}"

