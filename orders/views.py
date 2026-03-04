from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend

from .models import Order, OrderItem
from .serializers import OrderSerializer, OrderItemSerializer, OrderCreateUpdateSerializer


class OrderViewSet(viewsets.ModelViewSet):
    """ViewSet for Order model"""

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'warehouse']
    search_fields = ['order_number', 'customer_email', 'customer_name']
    ordering_fields = ['created_at', 'status', 'total_amount']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return orders for current user"""
        user = self.request.user
        return Order.objects.filter(owner=user).prefetch_related('items')

    @transaction.atomic
    def perform_create(self, serializer):
        """Create order with current user as owner"""
        serializer.save(owner=self.request.user)

    def create(self, request, *args, **kwargs):
        """Create order with items"""
        serializer = OrderCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data['owner'] = request.user

        order = serializer.save()
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """Update order"""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        serializer = OrderCreateUpdateSerializer(
            instance,
            data=request.data,
            partial=partial
        )
        serializer.is_valid(raise_exception=True)
        order = serializer.save()

        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'])
    def ship(self, request, pk=None):
        """Mark order as shipped"""
        order = self.get_object()

        if order.status != Order.PENDING and order.status != Order.CONFIRMED:
            return Response(
                {'error': f'Cannot ship order with status {order.status}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        order.status = Order.SHIPPED
        order.shipped_at = timezone.now()
        order.save()

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def deliver(self, request, pk=None):
        """Mark order as delivered"""
        order = self.get_object()

        if order.status != Order.SHIPPED:
            return Response(
                {'error': f'Order must be shipped before delivery'},
                status=status.HTTP_400_BAD_REQUEST
            )

        order.status = Order.DELIVERED
        order.delivered_at = timezone.now()
        order.save()

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel order"""
        order = self.get_object()

        if order.status in [Order.SHIPPED, Order.DELIVERED]:
            return Response(
                {'error': f'Cannot cancel {order.status.lower()} order'},
                status=status.HTTP_400_BAD_REQUEST
            )

        order.status = Order.CANCELLED
        order.save()

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['get'])
    def items(self, request, pk=None):
        """Get order items"""
        order = self.get_object()
        items = order.items.all()
        serializer = OrderItemSerializer(items, many=True)
        return Response(serializer.data)

