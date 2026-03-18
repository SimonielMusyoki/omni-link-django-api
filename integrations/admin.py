# pyright: reportMissingTypeArgument=false, reportUnknownMemberType=false
from django.contrib import admin

from .models import (
    Integration,
    ShopifyCredentials,
    OdooCredentials,
    QuickBooksCredentials,
    OrderSyncLog,
)


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'type', 'market', 'status', 'auto_sync_orders', 'warehouse', 'last_sync')
    list_filter = ('type', 'market', 'status', 'auto_sync_orders')
    search_fields = ('name', 'market')


@admin.register(OrderSyncLog)
class OrderSyncLogAdmin(admin.ModelAdmin):
    list_display = ('order', 'integration', 'target', 'status', 'created_at')
    list_filter = ('target', 'status', 'integration')
    search_fields = ('order__order_number', 'error_message')


admin.site.register(ShopifyCredentials)
admin.site.register(OdooCredentials)
admin.site.register(QuickBooksCredentials)
