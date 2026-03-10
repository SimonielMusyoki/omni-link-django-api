from rest_framework import serializers
from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    """Serializer for OrderItem model"""
    image_url = serializers.CharField(source='product.image_url', read_only=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'shopify_product_id', 'shopify_variant_id',
            'product_name', 'variant_name', 'sku', 'image_url', 'quantity', 'unit_price',
            'total_price', 'tax_amount', 'tax_rate', 'discount_amount',
            'fulfillment_status', 'requires_shipping', 'is_gift_card',
            'weight', 'weight_unit', 'vendor', 'properties'
        ]
        read_only_fields = ['id']


class OrderSerializer(serializers.ModelSerializer):
    """Serializer for Order model"""

    items = OrderItemSerializer(many=True, read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, allow_null=True)
    owner_email = serializers.CharField(source='owner.email', read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    is_paid = serializers.BooleanField(read_only=True)
    is_fulfilled = serializers.BooleanField(read_only=True)

    class Meta:
        model = Order
        fields = [
            # Basic Info
            'id', 'order_number', 'shopify_order_id', 'shopify_order_number',

            # Market & Currency
            'market', 'currency', 'exchange_rate',

            # Customer
            'customer_email', 'customer_name', 'customer_phone', 'shopify_customer_id',

            # Status
            'status', 'fulfillment_status', 'payment_status', 'delivery_status',

            # Payment
            'payment_method', 'order_channel', 'is_cash_on_delivery', 'payment_gateway', 'transaction_id',

            # Pricing
            'subtotal_price', 'total_tax', 'tax_rate', 'shipping_price',
            'discount_amount', 'total_amount',

            # Shipping Address
            'shipping_address_line1', 'shipping_address_line2', 'shipping_city',
            'shipping_state', 'shipping_postal_code', 'shipping_country', 'shipping_country_code',

            # Billing Address
            'billing_address_line1', 'billing_address_line2', 'billing_city',
            'billing_state', 'billing_postal_code', 'billing_country',

            # Shipping Info
            'shipping_method', 'tracking_number', 'tracking_url',

            # Shopify Specific
            'shopify_tags', 'shopify_note', 'customer_note', 'discount_codes',

            # Fulfillment
            'warehouse', 'warehouse_name', 'requires_shipping',

            # Metadata
            'owner', 'owner_email', 'shopify_created_at', 'shopify_updated_at',
            'created_at', 'updated_at', 'confirmed_at', 'fulfilled_at',
            'shipped_at', 'delivered_at', 'cancelled_at', 'cancel_reason',

            # Shopify Raw Data
            # 'shopify_raw_data',

            # Items & Computed
            'items', 'total_items', 'is_paid', 'is_fulfilled'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'owner', 'total_items', 'is_paid', 'is_fulfilled'
        ]


class OrderCreateUpdateSerializer(serializers.Serializer):
    """Serializer for creating/updating orders with items"""

    order_number = serializers.CharField(max_length=100)
    customer_email = serializers.EmailField()
    customer_name = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=Order.STATUS_CHOICES)
    order_channel = serializers.ChoiceField(
        choices=Order.ORDER_CHANNEL_CHOICES,
        required=False,
        default=Order.CHANNEL_WEBSITE,
    )
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

