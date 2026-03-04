from django.contrib import admin
from .models import ProductRequest


@admin.register(ProductRequest)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'type', 'status', 'requested_by', 'assigned_to', 'created_at')
    list_filter = ('status', 'type', 'requested_by', 'assigned_to', 'created_at')
    search_fields = ('title', 'description')
    readonly_fields = ('created_at', 'updated_at', 'approved_at')
    fieldsets = (
        ('Request Details', {
            'fields': ('title', 'description', 'type', 'status')
        }),
        ('Assignment', {
            'fields': ('requested_by', 'assigned_to', 'approved_by')
        }),
        ('Approval', {
            'fields': ('approved_at', 'rejection_reason')
        }),
        ('Related Items', {
            'fields': ('related_product', 'related_warehouse'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('metadata', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

