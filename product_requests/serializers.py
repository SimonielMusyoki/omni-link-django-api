from rest_framework import serializers
from .models import ProductRequest


class ProductRequestSerializer(serializers.ModelSerializer):
    """Serializer for Request model"""

    requested_by_email = serializers.CharField(source='requested_by.email', read_only=True)
    assigned_to_email = serializers.CharField(source='assigned_to.email', read_only=True, allow_null=True)
    approved_by_email = serializers.CharField(source='approved_by.email', read_only=True, allow_null=True)
    product_name = serializers.CharField(source='related_product.name', read_only=True, allow_null=True)
    warehouse_name = serializers.CharField(source='related_warehouse.name', read_only=True, allow_null=True)

    class Meta:
        model = ProductRequest
        fields = [
            'id', 'title', 'description', 'type', 'status',
            'requested_by', 'requested_by_email', 'assigned_to', 'assigned_to_email',
            'related_product', 'product_name', 'related_warehouse', 'warehouse_name',
            'metadata', 'created_at', 'updated_at', 'approved_at',
            'approved_by', 'approved_by_email', 'rejection_reason'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'approved_at', 'requested_by'
        ]

