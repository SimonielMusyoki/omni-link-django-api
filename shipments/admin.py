from django.contrib import admin
from .models import Shipment, ShipmentItem


class ShipmentItemInline(admin.TabularInline):
    """Inline admin for ShipmentItem"""
    model = ShipmentItem
    extra = 1
    readonly_fields = ('created_at', 'updated_at')
    fields = ('product', 'quantity', 'created_at', 'updated_at')


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ('tracking_number', 'origin', 'destination', 'status', 'created_by', 'created_at')
    list_filter = ('status', 'origin', 'destination', 'carrier', 'created_at')
    search_fields = ('tracking_number', 'carrier', 'notes')
    readonly_fields = ('tracking_number', 'created_at', 'updated_at', 'actual_delivery', 'created_by')
    fieldsets = (
        ('Tracking & Route', {
            'fields': ('tracking_number', 'origin', 'destination')
        }),
        ('Logistics', {
            'fields': ('status', 'carrier', 'estimated_delivery', 'actual_delivery', 'weight')
        }),
        ('Notes', {
            'fields': ('notes',)
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    inlines = [ShipmentItemInline]


@admin.register(ShipmentItem)
class ShipmentItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'shipment', 'product', 'quantity', 'created_at')
    list_filter = ('shipment__status', 'created_at')
    search_fields = ('product__name', 'product__sku', 'shipment__tracking_number')
    readonly_fields = ('created_at', 'updated_at')


