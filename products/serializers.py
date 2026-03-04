from rest_framework import serializers

from .models import (
    Category,
    Warehouse,
    Product,
    ProductBundle,
    Inventory,
    InventoryTransfer,
    Integration,
)


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------
class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
class WarehouseSerializer(serializers.ModelSerializer):
    manager_email = serializers.CharField(source='manager.email', read_only=True)
    total_stock = serializers.IntegerField(read_only=True)

    class Meta:
        model = Warehouse
        fields = [
            'id', 'name', 'location', 'address', 'capacity',
            'total_stock', 'manager', 'manager_email',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'manager']


# ---------------------------------------------------------------------------
# Bundle Items
# ---------------------------------------------------------------------------
class BundleItemSerializer(serializers.ModelSerializer):
    """Read/write serializer for a single component line inside a bundle."""
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_sku = serializers.CharField(source='component.sku', read_only=True)

    class Meta:
        model = ProductBundle
        fields = [
            'id', 'bundle', 'component', 'component_name', 'component_sku',
            'quantity',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        bundle = attrs.get('bundle') or getattr(self.instance, 'bundle', None)
        component = attrs.get('component') or getattr(self.instance, 'component', None)
        if bundle and component:
            if bundle.pk == component.pk:
                raise serializers.ValidationError('A bundle cannot contain itself.')
            if component.is_bundle:
                raise serializers.ValidationError(
                    'Nested bundles are not supported. Components must be regular products.'
                )
        return attrs


class BundleItemWriteSerializer(serializers.Serializer):
    """Inline component when creating / updating a bundle via ProductSerializer."""
    component = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1)


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------
class ProductSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    total_stock = serializers.IntegerField(read_only=True)
    needs_reorder = serializers.BooleanField(read_only=True)
    bundle_items = BundleItemSerializer(many=True, read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'sku', 'category', 'category_name',
            'price', 'reorder_level', 'image_url', 'is_bundle', 'bundle_items',
            'total_stock', 'needs_reorder',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AssembleBundleSerializer(serializers.Serializer):
    """Validates input for the assemble-bundle action."""
    warehouse = serializers.PrimaryKeyRelatedField(queryset=Warehouse.objects.all())
    quantity = serializers.IntegerField(min_value=1)


class DisassembleBundleSerializer(serializers.Serializer):
    """Validates input for the disassemble-bundle action."""
    warehouse = serializers.PrimaryKeyRelatedField(queryset=Warehouse.objects.all())
    quantity = serializers.IntegerField(min_value=1)


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------
class InventorySerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    available = serializers.IntegerField(read_only=True)

    class Meta:
        model = Inventory
        fields = [
            'id', 'product', 'product_name', 'product_sku',
            'warehouse', 'warehouse_name',
            'quantity', 'reserved', 'available',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class AddStockSerializer(serializers.Serializer):
    """Validates input for the add-stock action."""
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1)


class RemoveStockSerializer(serializers.Serializer):
    """Validates input for the remove-stock action."""
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.all())
    quantity = serializers.IntegerField(min_value=1)


# ---------------------------------------------------------------------------
# Inventory Transfer
# ---------------------------------------------------------------------------
class InventoryTransferSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    from_warehouse_name = serializers.CharField(source='from_warehouse.name', read_only=True)
    to_warehouse_name = serializers.CharField(source='to_warehouse.name', read_only=True)
    initiated_by_email = serializers.CharField(source='initiated_by.email', read_only=True)

    class Meta:
        model = InventoryTransfer
        fields = [
            'id', 'product', 'product_name',
            'from_warehouse', 'from_warehouse_name',
            'to_warehouse', 'to_warehouse_name',
            'quantity', 'status', 'notes',
            'initiated_by', 'initiated_by_email',
            'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = [
            'id', 'status', 'initiated_by',
            'created_at', 'updated_at', 'completed_at',
        ]

    def validate(self, attrs):
        if attrs.get('from_warehouse') == attrs.get('to_warehouse'):
            raise serializers.ValidationError(
                'Source and destination warehouse must be different.'
            )
        return attrs


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------
class IntegrationSerializer(serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)

    class Meta:
        model = Integration
        fields = [
            'id', 'name', 'type', 'status', 'api_key', 'api_secret',
            'webhook_url', 'warehouse', 'warehouse_name',
            'created_at', 'updated_at', 'last_sync',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'api_key': {'write_only': True},
            'api_secret': {'write_only': True},
        }


# ---------------------------------------------------------------------------
# Summary / report serializers (read-only)
# ---------------------------------------------------------------------------
class InventorySummarySerializer(serializers.Serializer):
    """Per-product summary across all warehouses."""
    product_id = serializers.IntegerField()
    product_name = serializers.CharField()
    product_sku = serializers.CharField()
    total_quantity = serializers.IntegerField()
    total_reserved = serializers.IntegerField()
    warehouse_count = serializers.IntegerField()
    needs_reorder = serializers.BooleanField()

