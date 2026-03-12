from rest_framework import serializers
from .models import ProductRequest, ProductRequestItem, ProductRequestEvent


class ProductRequestEventSerializer(serializers.ModelSerializer):
    """Serializer for ProductRequestEvent.

    Includes a ``label`` object with pre-computed presentational fields so the
    frontend timeline can render each event without its own mapping logic.
    """

    actor_email = serializers.CharField(source='actor.email', read_only=True, allow_null=True)
    label = serializers.SerializerMethodField()

    class Meta:
        model = ProductRequestEvent
        fields = [
            'id',
            'event_type',
            'actor',
            'actor_email',
            'note',
            'metadata',
            'created_at',
            'label',
        ]
        read_only_fields = fields

    def get_label(self, obj: ProductRequestEvent) -> dict:
        """Return presentational metadata for this event type.

        ``description`` is the ``note`` value when set so that per-instance
        context (e.g. "jane@example.com approved this request") takes priority
        over the generic template text.
        """
        defaults = ProductRequestEvent.EVENT_LABELS.get(
            obj.event_type,
            {
                'title': obj.get_event_type_display(),
                'description': '',
                'color': '#64748b',
                'icon_name': 'info',
            },
        )
        return {
            **defaults,
            # Note overrides the generic description when present.
            'description': obj.note if obj.note else defaults['description'],
        }


class ProductRequestItemSerializer(serializers.ModelSerializer):
    """Serializer for ProductRequestItem"""

    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)

    class Meta:
        model = ProductRequestItem
        fields = ['id', 'product', 'product_name', 'product_sku', 'quantity']
        read_only_fields = ['id']
        extra_kwargs = {
            'quantity': {'min_value': 1},
        }


class ProductRequestSerializer(serializers.ModelSerializer):
    """Serializer for ProductRequest model"""

    requested_by_email = serializers.CharField(source='requested_by.email', read_only=True)
    approver_email = serializers.CharField(source='approver.email', read_only=True, allow_null=True)
    approved_by_email = serializers.CharField(source='approved_by.email', read_only=True, allow_null=True)
    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True, allow_null=True)
    items = ProductRequestItemSerializer(many=True, required=False)
    timeline_events = ProductRequestEventSerializer(source='events', many=True, read_only=True)

    class Meta:
        model = ProductRequest
        fields = [
            'id', 'reason', 'status',
            'requested_by', 'requested_by_email',
            'approver', 'approver_email',
            'warehouse', 'warehouse_name',
            'items',
            'timeline_events',
            'created_at', 'updated_at', 'approved_at', 'ready_at',
            'approved_by', 'approved_by_email', 'rejection_reason'
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'approved_at', 'ready_at', 'requested_by'
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        items = attrs.get('items')

        if self.instance is None:
            errors = {}
            if not attrs.get('approver'):
                errors['approver'] = 'Approver is required.'
            if not attrs.get('warehouse'):
                errors['warehouse'] = 'Warehouse is required.'
            if not items:
                errors['items'] = 'At least one item is required.'
            if errors:
                raise serializers.ValidationError(errors)

        if items is not None:
            if len(items) == 0:
                raise serializers.ValidationError({'items': 'At least one item is required.'})

            product_ids = [item['product'].id for item in items if item.get('product')]
            if len(product_ids) != len(set(product_ids)):
                raise serializers.ValidationError({'items': 'Each product can only appear once per request.'})

        return attrs

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        req = ProductRequest.objects.create(**validated_data)
        ProductRequestItem.objects.bulk_create(
            [
                ProductRequestItem(
                    request=req,
                    product=item['product'],
                    quantity=item['quantity'],
                )
                for item in items_data
            ]
        )
        return req

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            ProductRequestItem.objects.bulk_create(
                [
                    ProductRequestItem(
                        request=instance,
                        product=item['product'],
                        quantity=item['quantity'],
                    )
                    for item in items_data
                ]
            )

        return instance

