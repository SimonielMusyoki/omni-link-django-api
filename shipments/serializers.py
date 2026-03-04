from rest_framework import serializers
from .models import Shipment


class ShipmentSerializer(serializers.ModelSerializer):
    """Serializer for Shipment model"""

    order_number = serializers.CharField(source='order.order_number', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    owner_email = serializers.CharField(source='owner.email', read_only=True)

    class Meta:
        model = Shipment
        fields = [
            'id', 'tracking_number', 'order', 'order_number',
            'warehouse', 'warehouse_name', 'status',
            'recipient_name', 'recipient_email', 'shipping_address',
            'shipping_city', 'shipping_state', 'shipping_zip', 'shipping_country',
            'carrier', 'estimated_delivery', 'actual_delivery', 'weight',
            'owner', 'owner_email', 'created_at', 'updated_at'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'actual_delivery', 'owner'
        ]

