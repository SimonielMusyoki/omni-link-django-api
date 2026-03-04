from django.contrib import admin
from .models import Invitation


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'warehouse', 'status', 'invited_by', 'invited_user', 'created_at')
    list_filter = ('status', 'invited_by', 'created_at')
    search_fields = ('email', 'warehouse__name')
    readonly_fields = ('token', 'created_at', 'updated_at', 'accepted_at')
    fieldsets = (
        ('Invitation Details', {
            'fields': ('email', 'warehouse', 'status')
        }),
        ('Users', {
            'fields': ('invited_by', 'invited_user')
        }),
        ('Token & Expiry', {
            'fields': ('token', 'expires_at')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'accepted_at'),
            'classes': ('collapse',)
        }),
    )

