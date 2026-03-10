from django.db import models


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

