from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Category,
    Warehouse,
    Product,
    ProductBundle,
    Inventory,
    InventoryTransfer,
    Market,
)


@admin.register(Market)
class MarketAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'currency', 'is_active', 'created_at')
    list_filter = ('is_active', 'currency')
    search_fields = ('name', 'code')
    ordering = ('name',)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('name', 'location', 'capacity', 'manager', 'created_at')
    list_filter = ('location',)
    search_fields = ('name', 'location')


class BundleItemInline(admin.TabularInline):
    model = ProductBundle
    fk_name = 'bundle'
    extra = 1
    raw_id_fields = ('component',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'category', 'price', 'is_bundle', 'reorder_level', 'image_preview_thumb', 'created_at')
    list_filter = ('category', 'is_bundle')
    search_fields = ('name', 'sku')
    readonly_fields = ('created_at', 'updated_at', 'image_preview')
    inlines = [BundleItemInline]

    def get_inlines(self, request, obj=None):
        """Only show bundle-item inline when editing a bundle."""
        if obj and obj.is_bundle:
            return [BundleItemInline]
        return []

    def image_preview_thumb(self, obj):
        if obj.image_url:
            return format_html('<img src="{}" height="40" />', obj.image_url)
        return '-'
    image_preview_thumb.short_description = 'Image'

    def image_preview(self, obj):
        if obj.image_url:
            return format_html('<img src="{}" width="200" />', obj.image_url)
        return 'No image'
    image_preview.short_description = 'Preview'


@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ('product', 'warehouse', 'quantity', 'reserved', 'available', 'updated_at')
    list_filter = ('warehouse',)
    search_fields = ('product__name', 'product__sku', 'warehouse__name')
    raw_id_fields = ('product', 'warehouse')

    def available(self, obj):
        return obj.available
    available.short_description = 'Available'


@admin.register(ProductBundle)
class ProductBundleAdmin(admin.ModelAdmin):
    list_display = ('bundle', 'component', 'quantity')
    list_filter = ('bundle',)
    search_fields = ('bundle__name', 'component__name')
    raw_id_fields = ('bundle', 'component')


@admin.register(InventoryTransfer)
class InventoryTransferAdmin(admin.ModelAdmin):
    list_display = ('id', 'product', 'from_warehouse', 'to_warehouse', 'quantity', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('product__name',)
    readonly_fields = ('created_at', 'updated_at', 'completed_at')
