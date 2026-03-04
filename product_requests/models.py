from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ProductRequest(models.Model):
    """Request model to store business requests (e.g., approval requests)"""

    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    REJECTED = 'REJECTED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (REJECTED, 'Rejected'),
    ]

    TRANSFER = 'TRANSFER'
    ADJUSTMENT = 'ADJUSTMENT'
    REFUND = 'REFUND'
    OTHER = 'OTHER'

    TYPE_CHOICES = [
        (TRANSFER, 'Transfer'),
        (ADJUSTMENT, 'Adjustment'),
        (REFUND, 'Refund'),
        (OTHER, 'Other'),
    ]

    id = models.AutoField(primary_key=True)
    title = models.CharField(max_length=255)
    description = models.TextField()
    type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requests_made')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests_assigned')
    related_product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests')
    related_warehouse = models.ForeignKey('products.Warehouse', on_delete=models.SET_NULL, null=True, blank=True, related_name='requests')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests_approved')
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['requested_by', '-created_at']),
            models.Index(fields=['assigned_to', 'status']),
            models.Index(fields=['type', '-created_at']),
        ]

    def __str__(self):
        return f"Request {self.id}: {self.title}"

