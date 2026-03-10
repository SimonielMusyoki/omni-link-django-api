from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend

from .models import Integration
from .serializers import IntegrationSerializer
from integrations.services import (
    test_integration_connection,
    import_shopify_orders,
    import_shopify_products,
    verify_shopify_webhook_hmac,
    process_shopify_webhook_event,
)


class IntegrationViewSet(viewsets.ModelViewSet):
    """CRUD API for Shopify, Odoo, and QuickBooks integrations."""

    queryset = (
        Integration.objects
        .select_related('warehouse')
        .prefetch_related(
            'shopify_credentials',
            'odoo_credentials',
            'quickbooks_credentials',
        )
    )
    serializer_class = IntegrationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['warehouse', 'type', 'status', 'market']
    search_fields = ['name', 'type', 'market']
    ordering_fields = ['created_at', 'status', 'market']
    ordering = ['-created_at']

    @action(detail=True, methods=['post'], url_path='sync')
    def sync(self, request, pk=None):
        integration = self.get_object()

        if integration.type == Integration.IntegrationType.SHOPIFY:
            # Accept ISO datetime or YYYY-MM-DD.
            raw_from = request.data.get('date_from')
            raw_to = request.data.get('date_to')

            def _parse_input(value, fallback):
                if not value:
                    return fallback
                parsed_dt = parse_datetime(str(value))
                if parsed_dt is not None:
                    return parsed_dt
                parsed_d = parse_date(str(value))
                if parsed_d is not None:
                    return datetime.combine(parsed_d, datetime.min.time(), tzinfo=dt_timezone.utc)
                return fallback

            date_to = _parse_input(raw_to, timezone.now())
            date_from = _parse_input(raw_from, date_to - timedelta(days=1))

            if date_from > date_to:
                return Response(
                    {'detail': 'date_from must be before date_to.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            try:
                stats = import_shopify_orders(
                    integration=integration,
                    owner=request.user,
                    created_at_min=date_from,
                    created_at_max=date_to,
                )
            except Exception as exc:  # noqa: BLE001
                integration.status = Integration.IntegrationStatus.ERROR
                integration.save(update_fields=['status', 'updated_at'])
                return Response(
                    {
                        'status': 'Sync failed',
                        'integration_id': integration.id,
                        'message': str(exc),
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    'status': 'Sync completed',
                    'integration_id': integration.id,
                    'last_sync': integration.last_sync,
                    'date_from': date_from,
                    'date_to': date_to,
                    'orders': stats,
                },
                status=status.HTTP_200_OK,
            )

        integration.last_sync = timezone.now()
        integration.save(update_fields=['last_sync', 'updated_at'])
        return Response(
            {
                'status': 'Sync initiated',
                'integration_id': integration.id,
                'last_sync': integration.last_sync,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'], url_path='test-connection')
    def test_connection(self, request, pk=None):
        integration = self.get_object()
        ok, message = test_integration_connection(integration)
        return Response(
            {
                'integration_id': integration.id,
                'success': ok,
                'message': message,
            },
            status=status.HTTP_200_OK if ok else status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=True, methods=['post'], url_path='sync-products')
    def sync_products(self, request, pk=None):
        integration = self.get_object()

        if integration.type != Integration.IntegrationType.SHOPIFY:
            return Response(
                {'detail': 'Product sync is only supported for Shopify integrations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stats = import_shopify_products(
                integration=integration,
                owner=request.user,
            )
        except Exception as exc:  # noqa: BLE001
            integration.status = Integration.IntegrationStatus.ERROR
            integration.save(update_fields=['status', 'updated_at'])
            return Response(
                {
                    'status': 'Product sync failed',
                    'integration_id': integration.id,
                    'message': str(exc),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                'status': 'Product sync completed',
                'integration_id': integration.id,
                'last_sync': integration.last_sync,
                'products': stats,
            },
            status=status.HTTP_200_OK,
        )


class ShopifyWebhookView(APIView):
    """Public webhook endpoint for Shopify order/product events."""

    authentication_classes = []
    permission_classes = [AllowAny]
    topic = None

    def post(self, request, *args, **kwargs):
        configured_topic = kwargs.get('topic') or self.topic
        webhook_topic = request.headers.get('X-Shopify-Topic') or configured_topic
        shop_domain = request.headers.get('X-Shopify-Shop-Domain', '')
        hmac_header = request.headers.get('X-Shopify-Hmac-Sha256', '')
        webhook_id = (request.headers.get('X-Shopify-Webhook-Id') or '').strip()
        secret = getattr(settings, 'SHOPIFY_WEBHOOK_SECRET', '')

        if not secret:
            return Response(
                {'detail': 'Shopify webhook secret is not configured.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not verify_shopify_webhook_hmac(request.body, hmac_header, secret):
            return Response(
                {'detail': 'Invalid Shopify webhook signature.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not webhook_topic:
            return Response(
                {'detail': 'Missing webhook topic.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not webhook_id:
            return Response(
                {'detail': 'Missing webhook id header (X-Shopify-Webhook-Id).'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = process_shopify_webhook_event(
                topic=webhook_topic,
                shop_domain=shop_domain,
                payload=request.data,
                webhook_id=webhook_id,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'accepted', **result}, status=status.HTTP_200_OK)
