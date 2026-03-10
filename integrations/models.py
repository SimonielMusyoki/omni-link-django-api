from django.db import models

from products.models import Warehouse


class Integration(models.Model):
    """Represents a market-scoped third-party integration."""

    class IntegrationType(models.TextChoices):
        SHOPIFY = 'SHOPIFY', 'Shopify'
        ODOO = 'ODOO', 'Odoo'
        QUICKBOOKS = 'QUICKBOOKS', 'QuickBooks'

    class IntegrationStatus(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'
        ERROR = 'ERROR', 'Error'

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=20, choices=IntegrationType.choices)
    market = models.CharField(max_length=100, help_text='Country/market name, e.g. Nigeria.')
    status = models.CharField(
        max_length=20,
        choices=IntegrationStatus.choices,
        default=IntegrationStatus.INACTIVE,
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='integrations',
    )
    last_sync = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['type', 'market'],
                name='unique_integration_type_per_market',
            ),
        ]
        indexes = [
            models.Index(fields=['type', 'market']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f'{self.get_type_display()} - {self.market}'


class ShopifyCredentials(models.Model):
    """Credentials required to connect a Shopify store."""

    integration = models.OneToOneField(
        Integration,
        on_delete=models.CASCADE,
        related_name='shopify_credentials',
    )
    store_url = models.URLField()
    access_token = models.CharField(max_length=512)
    api_version = models.CharField(max_length=20, default='2024-01')

    def __str__(self):
        return f'Shopify creds for {self.integration}'


class OdooCredentials(models.Model):
    """Credentials required to connect an Odoo instance."""

    integration = models.OneToOneField(
        Integration,
        on_delete=models.CASCADE,
        related_name='odoo_credentials',
    )
    server_url = models.URLField()
    database_url = models.CharField(max_length=255)
    company_id = models.CharField(max_length=255)
    email = models.EmailField()
    api_key = models.CharField(max_length=512)

    def __str__(self):
        return f'Odoo creds for {self.integration}'


class QuickBooksCredentials(models.Model):
    """Credentials required to connect a QuickBooks company."""

    class Environment(models.TextChoices):
        SANDBOX = 'SANDBOX', 'Sandbox'
        PRODUCTION = 'PRODUCTION', 'Production'

    integration = models.OneToOneField(
        Integration,
        on_delete=models.CASCADE,
        related_name='quickbooks_credentials',
    )
    realm_id = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    client_key = models.CharField(max_length=512)
    environment = models.CharField(
        max_length=20,
        choices=Environment.choices,
        default=Environment.SANDBOX,
    )

    def __str__(self):
        return f'QuickBooks creds for {self.integration}'


class ShopifyWebhookDelivery(models.Model):
    """Tracks webhook deliveries for idempotent Shopify webhook processing."""

    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        PROCESSED = 'PROCESSED', 'Processed'
        FAILED = 'FAILED', 'Failed'

    webhook_id = models.CharField(max_length=255, unique=True)
    topic = models.CharField(max_length=255)
    shop_domain = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
    )
    error_message = models.TextField(blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['topic', 'shop_domain']),
            models.Index(fields=['status', '-created_at']),
        ]

    def __str__(self):
        return f'{self.webhook_id} ({self.topic})'
