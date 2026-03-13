from django.db import models
from django.contrib.auth import get_user_model
from products.models import Warehouse

User = get_user_model()


class ProductRequest(models.Model):
    """Request model to store product requests with multiple items"""

    PENDING = 'PENDING'
    APPROVED = 'APPROVED'
    READY_TO_COLLECT = 'READY_TO_COLLECT'
    COLLECTED = 'COLLECTED'
    REJECTED = 'REJECTED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (APPROVED, 'Approved'),
        (READY_TO_COLLECT, 'Ready to Collect'),
        (COLLECTED, 'Collected'),
        (REJECTED, 'Rejected'),
    ]

    id = models.AutoField(primary_key=True)
    reason = models.TextField(help_text='Reason for this request')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requests_made')
    approver = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests_to_approve')
    warehouse = models.ForeignKey(Warehouse, on_delete=models.SET_NULL, null=True, blank=True, related_name='product_requests')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='requests_approved')
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['requested_by', '-created_at']),
            models.Index(fields=['approver', 'status']),
            models.Index(fields=['warehouse', 'status']),
        ]

    def __str__(self):
        return f"Request {self.id} by {self.requested_by.email}"


class ProductRequestEvent(models.Model):
    """Immutable timeline event for request actions and notification side effects."""

    REQUEST_CREATED = 'REQUEST_CREATED'
    REQUEST_APPROVED = 'REQUEST_APPROVED'
    REQUEST_REJECTED = 'REQUEST_REJECTED'
    REQUEST_READY_TO_COLLECT = 'REQUEST_READY_TO_COLLECT'
    REQUEST_COLLECTED = 'REQUEST_COLLECTED'
    EMAIL_TO_APPROVER_SENT = 'EMAIL_TO_APPROVER_SENT'
    EMAIL_TO_MANAGER_SENT = 'EMAIL_TO_MANAGER_SENT'
    EMAIL_TO_REQUESTER_SENT = 'EMAIL_TO_REQUESTER_SENT'

    EVENT_TYPE_CHOICES = [
        (REQUEST_CREATED, 'Request Created'),
        (REQUEST_APPROVED, 'Request Approved'),
        (REQUEST_REJECTED, 'Request Rejected'),
        (REQUEST_READY_TO_COLLECT, 'Request Ready to Collect'),
        (REQUEST_COLLECTED, 'Request Collected'),
        (EMAIL_TO_APPROVER_SENT, 'Approver Email Sent'),
        (EMAIL_TO_MANAGER_SENT, 'Manager Email Sent'),
        (EMAIL_TO_REQUESTER_SENT, 'Requester Email Sent'),
    ]

    # Presentational metadata consumed by the frontend timeline UI.
    # color: CSS hex; icon_name: lucide icon slug.
    EVENT_LABELS: dict = {
        REQUEST_CREATED: {
            'title': 'Request Created',
            'description': 'Request was submitted.',
            'color': '#3b82f6',
            'icon_name': 'clock',
        },
        REQUEST_APPROVED: {
            'title': 'Request Approved',
            'description': 'Request was approved.',
            'color': '#10b981',
            'icon_name': 'check-circle',
        },
        REQUEST_REJECTED: {
            'title': 'Request Rejected',
            'description': 'Request was rejected.',
            'color': '#ef4444',
            'icon_name': 'x-circle',
        },
        REQUEST_READY_TO_COLLECT: {
            'title': 'Ready to Collect',
            'description': 'Items are ready for collection.',
            'color': '#0ea5e9',
            'icon_name': 'package-check',
        },
        REQUEST_COLLECTED: {
            'title': 'Collected',
            'description': 'Items were collected by the requester.',
            'color': '#10b981',
            'icon_name': 'circle-check-big',
        },
        EMAIL_TO_APPROVER_SENT: {
            'title': 'Approval Email Sent',
            'description': 'An approval request email was sent to the approver.',
            'color': '#6366f1',
            'icon_name': 'mail',
        },
        EMAIL_TO_MANAGER_SENT: {
            'title': 'Manager Notified',
            'description': 'The warehouse manager was notified to prepare the items.',
            'color': '#6366f1',
            'icon_name': 'mail',
        },
        EMAIL_TO_REQUESTER_SENT: {
            'title': 'Requester Notified',
            'description': 'The requester was notified that items are ready.',
            'color': '#6366f1',
            'icon_name': 'mail',
        },
    }

    request = models.ForeignKey(
        ProductRequest,
        on_delete=models.CASCADE,
        related_name='events',
    )
    event_type = models.CharField(max_length=64, choices=EVENT_TYPE_CHOICES)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='product_request_events',
    )
    note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['request', 'created_at']),
            models.Index(fields=['event_type', 'created_at']),
        ]

    def __str__(self):
        return f"{self.event_type} for Request {self.request_id}"


class ProductRequestItem(models.Model):
    """Individual product item in a request"""

    request = models.ForeignKey(ProductRequest, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE, related_name='request_items')
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        unique_together = ['request', 'product']
        indexes = [
            models.Index(fields=['request', 'product']),
        ]

    def __str__(self):
        return f"{self.product.name} x{self.quantity} for Request {self.request.id}"

