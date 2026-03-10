from django.contrib import admin
from .models import ProductRequest, ProductRequestItem


class ProductRequestItemInline(admin.TabularInline):
    """Inline admin for ProductRequestItem"""
    model = ProductRequestItem
    extra = 1
    readonly_fields = ('created_at', 'updated_at')
    fields = ('product', 'quantity', 'created_at', 'updated_at')


@admin.register(ProductRequest)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'status', 'requested_by', 'approver', 'created_at')
    list_filter = ('status', 'requested_by', 'approver', 'created_at')
    search_fields = ('reason', 'requested_by__email', 'approver__email')
    readonly_fields = ('created_at', 'updated_at', 'approved_at', 'requested_by')
    fieldsets = (
        ('Request Details', {
            'fields': ('reason', 'status')
        }),
        ('Users', {
            'fields': ('requested_by', 'approver', 'approved_by')
        }),
        ('Approval', {
            'fields': ('approved_at', 'rejection_reason')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    inlines = [ProductRequestItemInline]


@admin.register(ProductRequestItem)
class ProductRequestItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'request', 'product', 'quantity', 'created_at')
    list_filter = ('request__status', 'created_at')
    search_fields = ('product__name', 'product__sku', 'request__id')
    readonly_fields = ('created_at', 'updated_at')


