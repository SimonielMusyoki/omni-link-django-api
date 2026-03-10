from django.contrib import admin

from .models import (
    Integration,
    ShopifyCredentials,
    OdooCredentials,
    QuickBooksCredentials,
)


@admin.register(Integration)
class IntegrationAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'type', 'market', 'status', 'warehouse', 'last_sync')
    list_filter = ('type', 'market', 'status')
    search_fields = ('name', 'market')


admin.site.register(ShopifyCredentials)
admin.site.register(OdooCredentials)
admin.site.register(QuickBooksCredentials)

