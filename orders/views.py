from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Prefetch

from .models import Order, OrderItem
from .serializers import OrderSerializer, OrderItemSerializer, OrderCreateUpdateSerializer
from integrations.models import Integration


class OrderViewSet(viewsets.ModelViewSet):
    """ViewSet for Order model"""

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'warehouse', 'created_at', 'shopify_created_at', 'market']
    search_fields = ['order_number', 'customer_email', 'customer_name']
    ordering_fields = ['shopify_created_at', 'created_at', 'status', 'total_amount']
    # Default sort: most recently placed on Shopify first.
    ordering = ['-shopify_created_at', '-created_at']

    def get_queryset(self):
        """Return orders for current user with optional date filtering"""
        user = self.request.user
        queryset = (
            Order.objects
            .filter(owner=user)
            .select_related('warehouse', 'owner', 'market')
            .prefetch_related(
                Prefetch('items', queryset=OrderItem.objects.select_related('product'))
            )
        )

        # Date range filtering - support both created_at and shopify_created_at
        created_at_gte = self.request.query_params.get('created_at__gte')
        created_at_lte = self.request.query_params.get('created_at__lte')
        shopify_created_at_gte = self.request.query_params.get('shopify_created_at__gte')
        shopify_created_at_lte = self.request.query_params.get('shopify_created_at__lte')

        if created_at_gte:
            queryset = queryset.filter(created_at__gte=created_at_gte)
        if created_at_lte:
            queryset = queryset.filter(created_at__lte=created_at_lte)
        if shopify_created_at_gte:
            queryset = queryset.filter(shopify_created_at__gte=shopify_created_at_gte)
        if shopify_created_at_lte:
            queryset = queryset.filter(shopify_created_at__lte=shopify_created_at_lte)

        return queryset

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

    @action(detail=True, methods=['post'], url_path='push-to-quickbooks')
    def push_to_quickbooks(self, request, pk=None):
        """Push order to QuickBooks for the order's market"""
        order = self.get_object()

        # Find active QuickBooks integration for this order's market
        integration = Integration.objects.filter(
            type=Integration.IntegrationType.QUICKBOOKS,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active QuickBooks integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # TODO: Implement actual QuickBooks API integration
        # For now, return success with integration info
        return Response({
            'success': True,
            'message': f'Order pushed to QuickBooks ({integration.name})',
            'integration_id': integration.id,
            'order_id': order.id,
        })

    @action(detail=True, methods=['get'], url_path='view-in-quickbooks')
    def view_in_quickbooks(self, request, pk=None):
        """Get QuickBooks URL for this order"""
        order = self.get_object()

        integration = Integration.objects.filter(
            type=Integration.IntegrationType.QUICKBOOKS,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active QuickBooks integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        creds = getattr(integration, 'quickbooks_credentials', None)
        if not creds:
            return Response(
                {'error': 'QuickBooks credentials not configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate QuickBooks URL
        env = 'sandbox' if creds.environment == 'SANDBOX' else 'app'
        url = f'https://{env}.qbo.intuit.com/app/invoice?txnId={order.shopify_order_id}'

        return Response({
            'url': url,
            'realm_id': creds.realm_id,
        })

    @action(detail=True, methods=['post'], url_path='create-odoo-so')
    def create_odoo_so(self, request, pk=None):
        """Create Odoo Sales Order for this order"""
        order = self.get_object()

        integration = Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # TODO: Implement actual Odoo XML-RPC integration
        return Response({
            'success': True,
            'message': f'Sales Order created in Odoo ({integration.name})',
            'integration_id': integration.id,
            'order_id': order.id,
            'odoo_so_id': f'SO{order.shopify_order_number}',
        })

    @action(detail=True, methods=['post'], url_path='create-odoo-invoice')
    def create_odoo_invoice(self, request, pk=None):
        """Create Odoo Invoice for this order"""
        order = self.get_object()

        integration = Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # TODO: Implement actual Odoo XML-RPC integration
        return Response({
            'success': True,
            'message': f'Invoice created in Odoo ({integration.name})',
            'integration_id': integration.id,
            'order_id': order.id,
            'odoo_invoice_id': f'INV{order.shopify_order_number}',
        })

    @action(detail=True, methods=['get'], url_path='view-odoo-so')
    def view_odoo_so(self, request, pk=None):
        """Get Odoo Sales Order URL for this order"""
        order = self.get_object()

        integration = Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        creds = getattr(integration, 'odoo_credentials', None)
        if not creds:
            return Response(
                {'error': 'Odoo credentials not configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate Odoo URL
        url = f'{creds.server_url}/web#id={order.shopify_order_id}&model=sale.order&view_type=form'

        return Response({
            'url': url,
            'server_url': creds.server_url,
        })

    @action(detail=True, methods=['get'], url_path='view-odoo-invoice')
    def view_odoo_invoice(self, request, pk=None):
        """Get Odoo Invoice URL for this order"""
        order = self.get_object()

        integration = Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first()

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        creds = getattr(integration, 'odoo_credentials', None)
        if not creds:
            return Response(
                {'error': 'Odoo credentials not configured'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate Odoo URL
        url = f'{creds.server_url}/web#id={order.shopify_order_id}&model=account.move&view_type=form'

        return Response({
            'url': url,
            'server_url': creds.server_url,
        })
