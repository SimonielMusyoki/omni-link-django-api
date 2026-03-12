from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ('product_name', 'variant_name', 'sku', 'quantity', 'unit_price', 'total_price', 'tax_amount', 'discount_amount')
    readonly_fields = ('product_name', 'variant_name', 'total_price')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number', 'market', 'customer_name', 'total_amount',
        'currency', 'order_channel', 'status', 'fulfillment_status', 'payment_status',
        'delivery_status', 'is_cash_on_delivery', 'created_at'
    )
    list_filter = (
        'market', 'order_channel', 'status', 'fulfillment_status',
        'payment_status', 'delivery_status', 'payment_method',
        'is_cash_on_delivery', 'warehouse', 'created_at'
    )
    search_fields = (
        'order_number', 'shopify_order_id', 'shopify_order_number',
        'customer_email', 'customer_name', 'customer_phone',
        'tracking_number', 'transaction_id'
    )
    readonly_fields = (
        'order_number', 'shopify_order_id', 'created_at', 'updated_at',
        'shopify_created_at', 'shopify_updated_at', 'confirmed_at',
        'fulfilled_at', 'shipped_at', 'delivered_at', 'cancelled_at',
        'owner', 'total_items', 'is_paid', 'is_fulfilled', 'currency_display'
    )
    inlines = [OrderItemInline]

    fieldsets = (
        ('Shopify Order Information', {
            'fields': (
                'order_number', 'shopify_order_id', 'shopify_order_number',
                'shopify_created_at', 'shopify_updated_at'
            )
        }),
        ('Market & Currency', {
            'fields': ('market', 'currency_display', 'exchange_rate')
        }),
        ('Customer Details', {
            'fields': (
                'customer_name', 'customer_email', 'customer_phone',
                'shopify_customer_id'
            )
        }),
        ('Status', {
            'fields': (
                'status', 'fulfillment_status', 'payment_status', 'delivery_status'
            )
        }),
        ('Payment Information', {
            'fields': (
                'payment_method', 'order_channel', 'is_cash_on_delivery', 'payment_gateway',
                'transaction_id'
            )
        }),
        ('Pricing', {
            'fields': (
                'subtotal_price', 'total_tax', 'tax_rate', 'shipping_price',
                'discount_amount', 'total_amount'
            )
        }),
        ('Shipping Address', {
            'fields': (
                'shipping_address_line1', 'shipping_address_line2',
                'shipping_city', 'shipping_state', 'shipping_postal_code',
                'shipping_country', 'shipping_country_code'
            )
        }),
        ('Billing Address', {
            'fields': (
                'billing_address_line1', 'billing_address_line2',
                'billing_city', 'billing_state', 'billing_postal_code',
                'billing_country'
            ),
            'classes': ('collapse',)
        }),
        ('Shipping Information', {
            'fields': ('shipping_method', 'tracking_number', 'tracking_url')
        }),
        ('Shopify Details', {
            'fields': (
                'shopify_tags', 'shopify_note', 'customer_note', 'discount_codes'
            ),
            'classes': ('collapse',)
        }),
        ('Fulfillment', {
            'fields': ('warehouse', 'requires_shipping')
        }),
        ('Additional Information', {
            'fields': ('cancel_reason',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': (
                'owner', 'created_at', 'updated_at', 'confirmed_at',
                'fulfilled_at', 'shipped_at', 'delivered_at', 'cancelled_at',
                'total_items', 'is_paid', 'is_fulfilled'
            ),
            'classes': ('collapse',)
        }),
        ('Raw Shopify Data', {
            'fields': ('shopify_raw_data',),
            'classes': ('collapse',)
        }),
    )

    def total_items(self, obj):
        """Display total items in admin"""
        return obj.total_items
    total_items.short_description = 'Total Items'

    def currency_display(self, obj):
        return obj.currency
    currency_display.short_description = 'Currency'

    def is_paid(self, obj):
        """Display payment status as boolean"""
        return obj.is_paid
    is_paid.boolean = True
    is_paid.short_description = 'Paid'

    def is_fulfilled(self, obj):
        """Display fulfillment status as boolean"""
        return obj.is_fulfilled
    is_fulfilled.boolean = True
    is_fulfilled.short_description = 'Fulfilled'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'order', 'product_name', 'variant_name', 'sku',
        'quantity', 'unit_price', 'total_price', 'fulfillment_status'
    )
    list_filter = ('fulfillment_status', 'requires_shipping', 'is_gift_card')
    search_fields = (
        'product_name', 'variant_name', 'sku',
        'shopify_product_id', 'shopify_variant_id', 'vendor'
    )
    readonly_fields = ('created_at', 'updated_at')
