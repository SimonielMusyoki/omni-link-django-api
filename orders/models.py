from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import MinValueValidator
from decimal import Decimal
from products.models import Market

User = get_user_model()


class Order(models.Model):
    """Order model to store Shopify orders from different markets"""

    # Order Status
    PENDING = 'PENDING'
    CONFIRMED = 'CONFIRMED'
    PROCESSING = 'PROCESSING'
    SHIPPED = 'SHIPPED'
    DELIVERED = 'DELIVERED'
    CANCELLED = 'CANCELLED'
    REFUNDED = 'REFUNDED'

    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (CONFIRMED, 'Confirmed'),
        (PROCESSING, 'Processing'),
        (SHIPPED, 'Shipped'),
        (DELIVERED, 'Delivered'),
        (CANCELLED, 'Cancelled'),
        (REFUNDED, 'Refunded'),
    ]

    # Fulfillment Status
    UNFULFILLED = 'UNFULFILLED'
    PARTIALLY_FULFILLED = 'PARTIALLY_FULFILLED'
    FULFILLED = 'FULFILLED'
    RESTOCKED = 'RESTOCKED'

    FULFILLMENT_STATUS_CHOICES = [
        (UNFULFILLED, 'Unfulfilled'),
        (PARTIALLY_FULFILLED, 'Partially Fulfilled'),
        (FULFILLED, 'Fulfilled'),
        (RESTOCKED, 'Restocked'),
    ]

    # Payment Status
    PENDING_PAYMENT = 'PENDING'
    AUTHORIZED = 'AUTHORIZED'
    PARTIALLY_PAID = 'PARTIALLY_PAID'
    PAID = 'PAID'
    PARTIALLY_REFUNDED = 'PARTIALLY_REFUNDED'
    REFUNDED_PAYMENT = 'REFUNDED'
    VOIDED = 'VOIDED'

    PAYMENT_STATUS_CHOICES = [
        (PENDING_PAYMENT, 'Pending'),
        (AUTHORIZED, 'Authorized'),
        (PARTIALLY_PAID, 'Partially Paid'),
        (PAID, 'Paid'),
        (PARTIALLY_REFUNDED, 'Partially Refunded'),
        (REFUNDED_PAYMENT, 'Refunded'),
        (VOIDED, 'Voided'),
    ]

    # Payment Method
    CASH_ON_DELIVERY = 'COD'
    PREPAID = 'PREPAID'
    CREDIT_CARD = 'CREDIT_CARD'
    BANK_TRANSFER = 'BANK_TRANSFER'
    MOBILE_MONEY = 'MOBILE_MONEY'
    OTHER = 'OTHER'

    PAYMENT_METHOD_CHOICES = [
        (CASH_ON_DELIVERY, 'Cash on Delivery'),
        (PREPAID, 'Prepaid'),
        (CREDIT_CARD, 'Credit Card'),
        (BANK_TRANSFER, 'Bank Transfer'),
        (MOBILE_MONEY, 'Mobile Money'),
        (OTHER, 'Other'),
    ]

    # Order Channel
    CHANNEL_WEBSITE = 'WEBSITE'
    CHANNEL_WHATSAPP = 'WHATSAPP'
    CHANNEL_POS = 'POS'

    ORDER_CHANNEL_CHOICES = [
        (CHANNEL_WEBSITE, 'Website'),
        (CHANNEL_WHATSAPP, 'Whatsapp'),
        (CHANNEL_POS, 'Hub POS'),
    ]

    # Delivery Status
    PENDING_DELIVERY = 'PENDING'
    IN_TRANSIT = 'IN_TRANSIT'
    OUT_FOR_DELIVERY = 'OUT_FOR_DELIVERY'
    DELIVERED_STATUS = 'DELIVERED'
    FAILED_DELIVERY = 'FAILED'
    RETURNED = 'RETURNED'

    DELIVERY_STATUS_CHOICES = [
        (PENDING_DELIVERY, 'Pending'),
        (IN_TRANSIT, 'In Transit'),
        (OUT_FOR_DELIVERY, 'Out for Delivery'),
        (DELIVERED_STATUS, 'Delivered'),
        (FAILED_DELIVERY, 'Failed'),
        (RETURNED, 'Returned'),
    ]

    # Basic Information
    id = models.AutoField(primary_key=True)
    order_number = models.CharField(max_length=100, unique=True, db_index=True)
    shopify_order_id = models.CharField(max_length=100, unique=True, db_index=True, help_text='Shopify Order ID')
    shopify_order_number = models.CharField(max_length=100, db_index=True, help_text='Shopify Order Number')

    # Market & Currency
    market = models.ForeignKey(
        Market,
        on_delete=models.PROTECT,
        related_name='orders',
        db_index=True,
        help_text='Market/Country for this order',
    )
    exchange_rate = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal('1.000000'),
        help_text='Exchange rate to base currency'
    )

    # Customer Information
    customer_email = models.EmailField()
    customer_name = models.CharField(max_length=255)
    customer_phone = models.CharField(max_length=50, blank=True)
    shopify_customer_id = models.CharField(max_length=100, blank=True, help_text='Shopify Customer ID')

    # Status Fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    fulfillment_status = models.CharField(
        max_length=30,
        choices=FULFILLMENT_STATUS_CHOICES,
        default=UNFULFILLED,
        db_index=True
    )
    payment_status = models.CharField(
        max_length=30,
        choices=PAYMENT_STATUS_CHOICES,
        default=PENDING_PAYMENT,
        db_index=True
    )
    delivery_status = models.CharField(
        max_length=30,
        choices=DELIVERY_STATUS_CHOICES,
        default=PENDING_DELIVERY,
        db_index=True
    )

    # Payment Information
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default=PREPAID
    )
    order_channel = models.CharField(
        max_length=20,
        choices=ORDER_CHANNEL_CHOICES,
        default=CHANNEL_WEBSITE,
        db_index=True,
        help_text='Sales channel for this Shopify order'
    )
    is_cash_on_delivery = models.BooleanField(default=False, help_text='True if payment method is COD')
    payment_gateway = models.CharField(max_length=100, blank=True, help_text='Payment gateway used (e.g., Stripe, PayPal)')
    transaction_id = models.CharField(max_length=255, blank=True, help_text='Payment transaction ID')

    # Pricing (in order currency)
    subtotal_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Subtotal before tax and shipping'
    )
    total_tax = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Total tax/VAT amount'
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Tax/VAT rate percentage'
    )
    shipping_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Shipping/delivery fee'
    )
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Total discount applied'
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Final total amount (subtotal + tax + shipping - discount)'
    )

    # Delivery Location
    shipping_address_line1 = models.CharField(max_length=255)
    shipping_address_line2 = models.CharField(max_length=255, blank=True)
    shipping_city = models.CharField(max_length=100)
    shipping_state = models.CharField(max_length=100, blank=True)
    shipping_postal_code = models.CharField(max_length=20, blank=True)
    shipping_country = models.CharField(max_length=100)
    shipping_country_code = models.CharField(max_length=2, blank=True, help_text='ISO 2-letter country code')

    # Billing Address (if different from shipping)
    billing_address_line1 = models.CharField(max_length=255, blank=True)
    billing_address_line2 = models.CharField(max_length=255, blank=True)
    billing_city = models.CharField(max_length=100, blank=True)
    billing_state = models.CharField(max_length=100, blank=True)
    billing_postal_code = models.CharField(max_length=20, blank=True)
    billing_country = models.CharField(max_length=100, blank=True)

    # Shipping Information
    shipping_method = models.CharField(max_length=100, blank=True, help_text='Shipping method/carrier')
    tracking_number = models.CharField(max_length=255, blank=True, help_text='Shipment tracking number')
    tracking_url = models.URLField(max_length=500, blank=True, help_text='Tracking URL')

    # Shopify Specific
    shopify_tags = models.TextField(blank=True, help_text='Comma-separated tags from Shopify')
    shopify_note = models.TextField(blank=True, help_text='Order note from Shopify')
    customer_note = models.TextField(blank=True, help_text='Note from customer')
    discount_codes = models.CharField(max_length=255, blank=True, help_text='Discount codes applied')

    # Fulfillment
    warehouse = models.ForeignKey(
        'products.Warehouse',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='orders',
        help_text='Warehouse handling this order'
    )
    requires_shipping = models.BooleanField(default=True)

    # Metadata
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    shopify_created_at = models.DateTimeField(null=True, blank=True, help_text='Order creation time in Shopify')
    shopify_updated_at = models.DateTimeField(null=True, blank=True, help_text='Last update time in Shopify')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True, help_text='Reason for cancellation')

    # Additional metadata stored as JSON
    shopify_raw_data = models.JSONField(default=dict, blank=True, help_text='Raw Shopify order data')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['order_number']),
            models.Index(fields=['shopify_order_id']),
            models.Index(fields=['market', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['fulfillment_status', '-created_at']),
            models.Index(fields=['payment_status', '-created_at']),
            models.Index(fields=['delivery_status', '-created_at']),
            models.Index(fields=['order_channel', '-created_at']),
            models.Index(fields=['owner', '-created_at']),
            models.Index(fields=['warehouse', '-created_at']),
            models.Index(fields=['customer_email']),
            models.Index(fields=['is_cash_on_delivery', 'status']),
        ]

    def __str__(self):
        return f"Order {self.order_number} ({self.market.name})"

    @property
    def currency(self):
        """Currency is sourced from the linked Market."""
        return self.market.currency if self.market_id else 'USD'

    @property
    def total_items(self):
        """Get total number of items in order"""
        return sum(item.quantity for item in self.items.all())

    @property
    def is_paid(self):
        """Check if order is fully paid"""
        return self.payment_status == self.PAID

    @property
    def is_fulfilled(self):
        """Check if order is fully fulfilled"""
        return self.fulfillment_status == self.FULFILLED


class OrderItem(models.Model):
    """OrderItem model to store items within an order"""

    id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey('products.Product', on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')

    # Shopify Product Information
    shopify_product_id = models.CharField(max_length=100, blank=True, help_text='Shopify Product ID')
    shopify_variant_id = models.CharField(max_length=100, blank=True, help_text='Shopify Variant ID')
    product_name = models.CharField(max_length=255, help_text='Product name at time of order')
    variant_name = models.CharField(max_length=255, blank=True, help_text='Variant name (e.g., Size: Large, Color: Red)')
    sku = models.CharField(max_length=100, blank=True, help_text='Product SKU')
    product_image_url = models.URLField(
        max_length=500,
        blank=True,
        null=True,
        help_text='Product image URL at time of order',
    )

    # Pricing
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Price per unit'
    )
    total_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text='Total price for this line item (quantity × unit_price)'
    )

    # Tax Information
    tax_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Tax amount for this item'
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Tax rate applied to this item'
    )

    # Discounts
    discount_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(0)],
        help_text='Discount applied to this item'
    )

    # Fulfillment
    fulfillment_status = models.CharField(max_length=30, default='UNFULFILLED', blank=True)
    requires_shipping = models.BooleanField(default=True)
    is_gift_card = models.BooleanField(default=False)

    # Weight and dimensions (for shipping calculation)
    weight = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Item weight in grams'
    )
    weight_unit = models.CharField(max_length=10, default='g', blank=True)

    # Additional metadata
    vendor = models.CharField(max_length=255, blank=True, help_text='Product vendor/supplier')
    properties = models.JSONField(default=dict, blank=True, help_text='Custom properties/line item properties')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['id']
        indexes = [
            models.Index(fields=['order']),
            models.Index(fields=['product']),
            models.Index(fields=['shopify_product_id']),
            models.Index(fields=['shopify_variant_id']),
            models.Index(fields=['sku']),
        ]

    def __str__(self):
        return f"{self.product_name} x{self.quantity} - Order {self.order.order_number}"

    @property
    def line_total_with_tax(self):
        """Calculate line total including tax"""
        return self.total_price + self.tax_amount

