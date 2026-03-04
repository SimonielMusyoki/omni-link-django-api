from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend

from .models import Shipment
from .serializers import ShipmentSerializer


class ShipmentViewSet(viewsets.ModelViewSet):
    """ViewSet for Shipment model"""

    serializer_class = ShipmentSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'order', 'warehouse']
    search_fields = ['tracking_number', 'recipient_name', 'recipient_email']
    ordering_fields = ['created_at', 'status', 'estimated_delivery']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return shipments for current user"""
        user = self.request.user
        return Shipment.objects.filter(owner=user)

    def perform_create(self, serializer):
        """Create shipment with current user as owner"""
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def mark_in_transit(self, request, pk=None):
        """Mark shipment as in transit"""
        shipment = self.get_object()

        if shipment.status != Shipment.PROCESSING:
            return Response(
                {'error': 'Can only mark PROCESSING shipments as in transit'},
                status=status.HTTP_400_BAD_REQUEST
            )

        shipment.status = Shipment.IN_TRANSIT
        shipment.save()

        return Response(
            ShipmentSerializer(shipment).data,
            status=status.HTTP_200_OK
        )

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

