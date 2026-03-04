from rest_framework import serializers
from products.models import Integration


class IntegrationSerializer(serializers.ModelSerializer):
    """Serializer for Integration model"""

    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    owner_email = serializers.CharField(source='owner.email', read_only=True)

    class Meta:
        model = Integration
        fields = [
            'id', 'name', 'type', 'status', 'api_key', 'api_secret',
            'webhook_url', 'warehouse', 'warehouse_name', 'owner', 'owner_email',
            'created_at', 'updated_at', 'last_sync'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'owner']
        extra_kwargs = {
            'api_key': {'write_only': True},
            'api_secret': {'write_only': True},
        }

