from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
import uuid

from .models import Shipment, ShipmentItem
from .serializers import ShipmentSerializer
from products.models import Inventory


class ShipmentViewSet(viewsets.ModelViewSet):
    """ViewSet for Shipment model"""

    serializer_class = ShipmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'origin', 'destination']
    search_fields = ['tracking_number', 'carrier', 'notes']
    ordering_fields = ['created_at', 'status', 'estimated_delivery']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return all shipments (users can see all shipments)"""
        return (
            Shipment.objects
            .select_related('origin', 'destination', 'created_by')
            .prefetch_related('items__product')
        )

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create shipment with items"""
        data = request.data

        # Validate required fields
        if not data.get('origin'):
            return Response(
                {'error': 'Origin warehouse is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not data.get('destination'):
            return Response(
                {'error': 'Destination warehouse is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if data.get('origin') == data.get('destination'):
            return Response(
                {'error': 'Origin and destination cannot be the same'},
                status=status.HTTP_400_BAD_REQUEST
            )

        items_data = data.get('items', [])
        if not items_data:
            return Response(
                {'error': 'At least one item is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate tracking number
        tracking_number = f"SHP-{uuid.uuid4().hex[:12].upper()}"

        # Create shipment
        shipment = Shipment.objects.create(
            tracking_number=tracking_number,
            origin_id=data.get('origin'),
            destination_id=data.get('destination'),
            carrier=data.get('carrier', ''),
            estimated_delivery=data.get('estimated_delivery'),
            weight=data.get('weight'),
            notes=data.get('notes', ''),
            created_by=request.user
        )

        # Create shipment items
        for item_data in items_data:
            ShipmentItem.objects.create(
                shipment=shipment,
                product_id=item_data.get('product'),
                quantity=item_data.get('quantity')
            )

        serializer = self.get_serializer(shipment)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update shipment status"""
        shipment = self.get_object()
        new_status = request.data.get('status')

        if not new_status:
            return Response(
                {'error': 'Status is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate status
        valid_statuses = [choice[0] for choice in Shipment.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Must be one of: {", ".join(valid_statuses)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        old_status = shipment.status
        shipment.status = new_status

        # If status is RECEIVED, update inventory and set actual delivery
        if new_status == Shipment.RECEIVED and old_status != Shipment.RECEIVED:
            self._update_inventory_on_receipt(shipment)
            shipment.actual_delivery = timezone.now()

        shipment.save()

        serializer = self.get_serializer(shipment)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @transaction.atomic
    def _update_inventory_on_receipt(self, shipment):
        """Update inventory when shipment is received"""
        for item in shipment.items.all():
            # Get or create inventory for destination warehouse
            inventory, created = Inventory.objects.get_or_create(
                product=item.product,
                warehouse=shipment.destination,
                defaults={'quantity': 0}
            )

            # Add quantity to destination
            inventory.quantity += item.quantity
            inventory.save()

            # Optionally: Reduce inventory from origin warehouse
            # (Uncomment if you want to track inventory at origin as well)
            # origin_inventory = Inventory.objects.filter(
            #     product=item.product,
            #     warehouse=shipment.origin
            # ).first()
            # if origin_inventory:
            #     origin_inventory.quantity = max(0, origin_inventory.quantity - item.quantity)
            #     origin_inventory.save()


    @action(detail=True, methods=['post'])
    def mark_delivered(self, request, pk=None):
        """Mark shipment as delivered"""
        shipment = self.get_object()

        if shipment.status != Shipment.IN_TRANSIT:
            return Response(
                {'error': 'Can only mark IN_TRANSIT shipments as delivered'},
                status=status.HTTP_400_BAD_REQUEST
            )

        shipment.status = Shipment.DELIVERED
        shipment.actual_delivery = timezone.now()
        shipment.save()

        return Response(
            ShipmentSerializer(shipment).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def mark_returned(self, request, pk=None):
        """Mark shipment as returned"""
        shipment = self.get_object()

        if shipment.status == Shipment.DELIVERED:
            shipment.status = Shipment.RETURNED
            shipment.save()

            return Response(
                ShipmentSerializer(shipment).data,
                status=status.HTTP_200_OK
            )

        return Response(
            {'error': 'Can only return DELIVERED shipments'},
            status=status.HTTP_400_BAD_REQUEST
        )

    @action(detail=True, methods=['post'])
    def mark_failed(self, request, pk=None):
        """Mark shipment as failed"""
        shipment = self.get_object()

        shipment.status = Shipment.FAILED
        shipment.save()

        return Response(
            ShipmentSerializer(shipment).data,
            status=status.HTTP_200_OK
        )
