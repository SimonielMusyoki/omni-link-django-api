# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportUnnecessaryCast=false, reportIncompatibleMethodOverride=false

from typing import Any, cast

from django.db.models import Prefetch, QuerySet
from django.utils import timezone
from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.serializers import BaseSerializer

from .models import Order, OrderItem
from .serializers import OrderSerializer, OrderItemSerializer, OrderCreateUpdateSerializer
from api.timezones import parse_business_datetime_filter_value
from integrations.models import Integration
from integrations.services import (
    create_odoo_sales_order,
    create_odoo_invoice_record,
    create_quickbooks_sales_invoice,
)


class OrderViewSet(viewsets.ModelViewSet[Order]):
    """ViewSet for Order model"""

    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'warehouse', 'created_at', 'shopify_created_at', 'market']
    search_fields = ['order_number', 'customer_email', 'customer_name']
    ordering_fields = ['shopify_created_at', 'created_at', 'status', 'total_amount']
    # Default sort: most recently placed on Shopify first.
    ordering = ['-shopify_created_at', '-created_at']

    def get_queryset(self) -> QuerySet[Order]:
        """Return orders for current user with optional date filtering"""
        queryset = (
            Order.objects
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
            queryset = queryset.filter(
                created_at__gte=parse_business_datetime_filter_value(created_at_gte, end_of_day=False)
            )
        if created_at_lte:
            queryset = queryset.filter(
                created_at__lte=parse_business_datetime_filter_value(created_at_lte, end_of_day=True)
            )
        if shopify_created_at_gte:
            queryset = queryset.filter(
                shopify_created_at__gte=parse_business_datetime_filter_value(
                    shopify_created_at_gte,
                    end_of_day=False,
                )
            )
        if shopify_created_at_lte:
            queryset = queryset.filter(
                shopify_created_at__lte=parse_business_datetime_filter_value(
                    shopify_created_at_lte,
                    end_of_day=True,
                )
            )

        return queryset

    @transaction.atomic
    def perform_create(self, serializer: BaseSerializer[Order]) -> None:
        """Create order with current user as owner"""
        serializer.save(owner=self.request.user)

    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Create order with items"""
        serializer = OrderCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validated_data['owner'] = request.user

        order = cast(Order, serializer.save())
        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """Update order"""
        partial = kwargs.pop('partial', False)
        instance = cast(Order, self.get_object())

        serializer = OrderCreateUpdateSerializer(
            instance,
            data=request.data,
            partial=partial
        )
        serializer.is_valid(raise_exception=True)
        order = cast(Order, serializer.save())

        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=['post'])
    def ship(self, request: Request, pk: str | None = None) -> Response:
        """Mark order as shipped"""
        order = cast(Order, self.get_object())

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
    def deliver(self, request: Request, pk: str | None = None) -> Response:
        """Mark order as delivered"""
        order = cast(Order, self.get_object())

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
    def cancel(self, request: Request, pk: str | None = None) -> Response:
        """Cancel order"""
        order = cast(Order, self.get_object())

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
    def items(self, request: Request, pk: str | None = None) -> Response:
        """Get order items"""
        order = cast(Order, self.get_object())
        items = order.items.all()
        serializer = OrderItemSerializer(items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='push-to-quickbooks')
    def push_to_quickbooks(self, request: Request, pk: str | None = None) -> Response:
        """Push order to QuickBooks for the order's market"""
        order = cast(Order, self.get_object())

        # Find active QuickBooks integration for this order's market
        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.QUICKBOOKS,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

        if not integration:
            return Response(
                {'error': f'No active QuickBooks integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            quickbooks_invoice_id = create_quickbooks_sales_invoice(integration, order)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {'error': f'Failed to create QuickBooks Invoice: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.quickbooks_sales_invoice_id = str(quickbooks_invoice_id)
        order.save(update_fields=['quickbooks_sales_invoice_id'])

        serializer = OrderSerializer(order)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='view-in-quickbooks')
    def view_in_quickbooks(self, request: Request, pk: str | None = None) -> Response:
        """Get QuickBooks URL for this order"""
        order = cast(Order, self.get_object())

        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.QUICKBOOKS,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

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

        if not order.quickbooks_sales_invoice_id:
            return Response(
                {'error': 'No QuickBooks Invoice has been created for this order yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Generate QuickBooks URL
        env = 'sandbox' if creds.environment == 'SANDBOX' else 'app'
        url = (
            f'https://{env}.qbo.intuit.com/app/invoice'
            f'?txnId={order.quickbooks_sales_invoice_id}'
        )

        return Response({
            'url': url,
            'realm_id': creds.realm_id,
        })

    @action(detail=True, methods=['post'], url_path='create-odoo-so')
    def create_odoo_so(self, request: Request, pk: str | None = None) -> Response:
        """Create Odoo Sales Order for this order"""
        order = cast(Order, self.get_object())

        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            odoo_so_id = create_odoo_sales_order(integration, order)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {'error': f'Failed to create Odoo Sales Order: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.odoo_sales_order_id = str(odoo_so_id)
        order.save(update_fields=['odoo_sales_order_id'])

        serializer = OrderSerializer(order)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='create-odoo-invoice')
    def create_odoo_invoice(self, request: Request, pk: str | None = None) -> Response:
        """Create Odoo Invoice for this order"""
        order = cast(Order, self.get_object())

        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

        if not integration:
            return Response(
                {'error': f'No active Odoo integration found for {order.market.name}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            odoo_invoice_id = create_odoo_invoice_record(integration, order)
        except Exception as exc:  # noqa: BLE001
            return Response(
                {'error': f'Failed to create Odoo Invoice: {exc}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.odoo_sales_invoice_id = str(odoo_invoice_id)
        order.save(update_fields=['odoo_sales_invoice_id'])

        serializer = OrderSerializer(order)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='view-odoo-so')
    def view_odoo_so(self, request: Request, pk: str | None = None) -> Response:
        """Get Odoo Sales Order URL for this order"""
        order = cast(Order, self.get_object())

        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

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

        if not order.odoo_sales_order_id:
            return Response(
                {'error': 'No Odoo Sales Order has been created for this order yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = (
            f"{creds.server_url.rstrip('/')}/web"
            f"#id={order.odoo_sales_order_id}&model=sale.order&view_type=form"
        )

        return Response({
            'url': url,
            'server_url': creds.server_url,
        })

    @action(detail=True, methods=['get'], url_path='view-odoo-invoice')
    def view_odoo_invoice(self, request: Request, pk: str | None = None) -> Response:
        """Get Odoo Invoice URL for this order"""
        order = cast(Order, self.get_object())

        integration = cast(Integration | None, Integration.objects.filter(
            type=Integration.IntegrationType.ODOO,
            market=order.market.name,
            status=Integration.IntegrationStatus.ACTIVE,
        ).first())

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

        if not order.odoo_sales_invoice_id:
            return Response(
                {'error': 'No Odoo Invoice has been created for this order yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        url = (
            f"{creds.server_url.rstrip('/')}/web"
            f"#id={order.odoo_sales_invoice_id}&model=account.move&view_type=form"
        )

        return Response({
            'url': url,
            'server_url': creds.server_url,
        })
