from rest_framework import serializers
from .models import Shipment, ShipmentItem


class ShipmentItemSerializer(serializers.ModelSerializer):
    """Serializer for ShipmentItem model"""

    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = ShipmentItem
        fields = ['id', 'product', 'product_name', 'product_sku', 'quantity']
        read_only_fields = ['id']


class ShipmentSerializer(serializers.ModelSerializer):
    """Serializer for Shipment model"""

    origin_name = serializers.CharField(source='origin.name', read_only=True)
    origin_location = serializers.CharField(source='origin.location', read_only=True)
    destination_name = serializers.CharField(source='destination.name', read_only=True)
    destination_location = serializers.CharField(source='destination.location', read_only=True)
    created_by_email = serializers.CharField(source='created_by.email', read_only=True)
    items = ShipmentItemSerializer(many=True, read_only=True)

    class Meta:
        model = Shipment
        fields = [
            'id', 'tracking_number', 'origin', 'origin_name', 'origin_location',
            'destination', 'destination_name', 'destination_location',
            'status', 'carrier', 'estimated_delivery', 'actual_delivery',
            'weight', 'notes', 'created_by', 'created_by_email',
            'items', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'tracking_number', 'created_at', 'updated_at',
            'actual_delivery', 'created_by'
        ]



