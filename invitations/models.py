from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Invitation(models.Model):
    """Invitation model to track warehouse invitations"""

    PENDING = 'PENDING'
    ACCEPTED = 'ACCEPTED'
    REJECTED = 'REJECTED'
    EXPIRED = 'EXPIRED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (ACCEPTED, 'Accepted'),
        (REJECTED, 'Rejected'),
        (EXPIRED, 'Expired'),
    ]

    id = models.AutoField(primary_key=True)
    email = models.EmailField()
    warehouse = models.ForeignKey('products.Warehouse', on_delete=models.CASCADE, related_name='invitations')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='invitations_sent')
    invited_user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='invitations_received')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    token = models.CharField(max_length=500, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email', 'warehouse']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['token']),
            models.Index(fields=['invited_by', '-created_at']),
        ]
        unique_together = [['email', 'warehouse']]

    def __str__(self):
        return f"Invitation {self.id}: {self.email} to {self.warehouse.name}"

