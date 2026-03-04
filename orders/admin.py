from django.contrib import admin
from .models import Order, OrderItem


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ('product', 'quantity', 'unit_price', 'total_price')
    readonly_fields = ('total_price',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer_name', 'status', 'total_amount', 'warehouse', 'owner', 'created_at')
    list_filter = ('status', 'owner', 'warehouse', 'created_at')
    search_fields = ('order_number', 'customer_email', 'customer_name')
    readonly_fields = ('created_at', 'updated_at', 'shipped_at', 'delivered_at')
    inlines = [OrderItemInline]
    fieldsets = (
        ('Order Information', {
            'fields': ('order_number', 'status')
        }),
        ('Customer Details', {
            'fields': ('customer_name', 'customer_email')
        }),
        ('Order Details', {
            'fields': ('total_amount', 'warehouse', 'owner')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'shipped_at', 'delivered_at'),
            'classes': ('collapse',)
        }),
    )

