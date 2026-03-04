from django.contrib import admin
from .models import Shipment


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('tracking_number', 'order', 'status', 'recipient_name', 'owner', 'created_at')
    list_filter = ('status', 'owner', 'carrier', 'created_at')
    search_fields = ('tracking_number', 'recipient_name', 'recipient_email')
    readonly_fields = ('created_at', 'updated_at', 'actual_delivery')
    fieldsets = (
        ('Tracking & Order', {
            'fields': ('tracking_number', 'order', 'warehouse')
        }),
        ('Recipient', {
            'fields': ('recipient_name', 'recipient_email')
        }),
        ('Shipping Address', {
            'fields': ('shipping_address', 'shipping_city', 'shipping_state', 'shipping_zip', 'shipping_country')
        }),
        ('Logistics', {
            'fields': ('status', 'carrier', 'estimated_delivery', 'actual_delivery', 'weight')
        }),
        ('Metadata', {
            'fields': ('owner', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

