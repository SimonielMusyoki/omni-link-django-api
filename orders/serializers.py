from rest_framework import serializers
from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    """Serializer for OrderItem model"""

    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'order', 'product', 'product_name', 'product_sku',
            'quantity', 'unit_price', 'total_price'
        ]
        read_only_fields = ['id']


class OrderSerializer(serializers.ModelSerializer):
    """Serializer for Order model"""

    items = OrderItemSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    owner_email = serializers.CharField(source='owner.email', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'customer_email', 'customer_name',
            'status', 'total_amount', 'warehouse', 'warehouse_name',
            'owner', 'owner_email', 'created_at', 'updated_at',
            'shipped_at', 'delivered_at', 'items'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'shipped_at', 'delivered_at', 'owner'
        ]


class OrderCreateUpdateSerializer(serializers.Serializer):
    """Serializer for creating/updating orders with items"""

    order_number = serializers.CharField(max_length=100)
    customer_email = serializers.EmailField()
    customer_name = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=Order.STATUS_CHOICES)
    total_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    warehouse = serializers.IntegerField()
    items = OrderItemSerializer(many=True)

    def create(self, validated_data):
        """Create order with items"""
        items_data = validated_data.pop('items')
        order = Order.objects.create(**validated_data)

        for item_data in items_data:
            OrderItem.objects.create(order=order, **item_data)

        return order

    def update(self, instance, validated_data):
        """Update order"""
        items_data = validated_data.pop('items', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                OrderItem.objects.create(order=instance, **item_data)

        return instance

