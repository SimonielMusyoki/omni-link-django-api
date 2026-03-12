from django.db import transaction
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    Category,
    Warehouse,
    Product,
    ProductBundle,
    Inventory,
    InventoryTransfer,
    Market,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Market
# ---------------------------------------------------------------------------
class MarketSerializer(serializers.ModelSerializer):
    """Serializer for Market model"""

    class Meta:
        model = Market
        fields = [
            'id',
            'name',
            'code',
            'currency',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_code(self, value):
        """Ensure code is uppercase"""
        return value.upper()


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
    manager = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(is_active=True),
        required=False,
    )
    manager_email = serializers.CharField(source='manager.email', read_only=True)
    total_stock = serializers.SerializerMethodField()

    class Meta:
        model = Warehouse
        fields = [
            'id', 'name', 'location', 'address', 'capacity',
            'total_stock', 'manager', 'manager_email',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_total_stock(self, obj):
        annotated = getattr(obj, 'annotated_total_stock', None)
        if annotated is not None:
            return int(annotated)
        return obj.total_stock


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

    def to_internal_value(self, data):
        if hasattr(data, 'get') and data.get('kit') is not None and data.get('bundle') is None:
            mapped = data.copy() if hasattr(data, 'copy') else dict(data)
            mapped['bundle'] = mapped.get('kit')
            if hasattr(mapped, 'pop'):
                mapped.pop('kit', None)
            data = mapped
        return super().to_internal_value(data)

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
    total_stock = serializers.SerializerMethodField()
    needs_reorder = serializers.SerializerMethodField()
    bundle_items = BundleItemSerializer(many=True, read_only=True)
    is_kit = serializers.BooleanField(source='is_bundle', required=False)
    kit_items = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'sku', 'category', 'category_name',
            'price', 'reorder_level', 'image_url', 'is_bundle', 'is_kit', 'is_physical',
            'bundle_items', 'kit_items',
            'total_stock', 'needs_reorder',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_total_stock(self, obj):
        annotated = getattr(obj, 'annotated_total_stock', None)
        if annotated is not None:
            return int(annotated)
        return obj.total_stock

    def get_needs_reorder(self, obj):
        if not obj.is_physical:
            return False

        annotated = getattr(obj, 'annotated_total_stock', None)
        if annotated is not None:
            return int(annotated) <= obj.reorder_level

        return obj.needs_reorder

    def validate(self, attrs):
        attrs = super().validate(attrs)
        is_physical = attrs.get('is_physical')
        if is_physical is None and self.instance is not None:
            is_physical = self.instance.is_physical

        if is_physical is False:
            attrs['reorder_level'] = 0

        return attrs

    def get_kit_items(self, obj):
        return BundleItemSerializer(obj.bundle_items.all(), many=True).data


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
        validators = []

    def update(self, instance, validated_data):
        target_warehouse = validated_data.get('warehouse')
        if target_warehouse and target_warehouse.pk != instance.warehouse_id:
            with transaction.atomic():
                conflict = Inventory.objects.select_for_update().filter(
                    product=instance.product,
                    warehouse=target_warehouse,
                ).exclude(pk=instance.pk).first()
                if conflict:
                    instance.quantity += conflict.quantity
                    instance.reserved += conflict.reserved
                    conflict.delete()
                instance.warehouse = target_warehouse
                if 'quantity' in validated_data:
                    instance.quantity = validated_data['quantity']
                if 'reserved' in validated_data:
                    instance.reserved = validated_data['reserved']
                instance.full_clean()
                instance.save()
                return instance
        return super().update(instance, validated_data)


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
