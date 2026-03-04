from rest_framework import serializers
from .models import Invitation


class InvitationSerializer(serializers.ModelSerializer):
    """Serializer for Invitation model"""

    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    invited_by_email = serializers.CharField(source='invited_by.email', read_only=True)
    invited_user_email = serializers.CharField(source='invited_user.email', read_only=True, allow_null=True)

    class Meta:
        model = Invitation
        fields = [
            'id', 'email', 'warehouse', 'warehouse_name',
            'invited_by', 'invited_by_email', 'invited_user', 'invited_user_email',
            'status', 'token', 'expires_at', 'created_at', 'updated_at', 'accepted_at'
        ]
        read_only_fields = [
            'id', 'token', 'created_at', 'updated_at', 'accepted_at', 'invited_by'
        ]

