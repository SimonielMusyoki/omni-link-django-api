# pyright: reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportMissingTypeArgument=false
import base64
import logging
import secrets
from datetime import datetime
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
import requests
from rest_framework import status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from django_filters.rest_framework import DjangoFilterBackend
from api.timezones import parse_business_datetime_filter_value
from authentication.permissions import IsAdminOrOwner

logger = logging.getLogger(__name__)

from .models import Integration, ProductMarketMapping, OrderSyncLog
from .serializers import (
    IntegrationSerializer,
    ProductMarketMappingSerializer,
    OrderSyncLogSerializer,
)
from integrations.services import (
    INTUIT_TOKEN_URL,
    INTUIT_AUTH_URL,
    test_integration_connection,
    import_shopify_orders,
    import_shopify_products,
    verify_shopify_webhook_hmac,
    process_shopify_webhook_event,
    resolve_shopify_integration_by_shop_domain,
)


# ---------------------------------------------------------------------------
# QuickBooks OAuth callback (public endpoint, no auth required)
# ---------------------------------------------------------------------------
class QuickBooksOAuthCallbackView(APIView):
    """Handles the OAuth 2.0 redirect from Intuit after user authorization."""

    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        from .models import Integration

        code = request.query_params.get('code')
        raw_state = request.query_params.get('state', '')
        realm_id = request.query_params.get('realmId')

        if not code or not raw_state:
            return Response(
                {'error': 'Missing code or state parameter.'},
                status=400,
            )

        # Parse composite state: "<integration_id>:<csrf_token>"
        parts = raw_state.split(':', 1)
        if len(parts) != 2:
            return Response(
                {'error': 'Malformed state parameter.'},
                status=400,
            )

        try:
            integration_id = int(parts[0])
            csrf_token = parts[1]
        except (ValueError, TypeError):
            return Response(
                {'error': 'Invalid integration ID in state parameter.'},
                status=400,
            )

        try:
            integration = Integration.objects.get(pk=integration_id)
        except Integration.DoesNotExist:
            return Response(
                {'error': 'Invalid integration ID in state parameter.'},
                status=400,
            )

        creds = getattr(integration, 'quickbooks_credentials', None)
        if not creds:
            return Response(
                {'error': 'QuickBooks credentials not configured for this integration.'},
                status=400,
            )

        # Validate CSRF state token
        if not creds.oauth_state or not secrets.compare_digest(creds.oauth_state, csrf_token):
            return Response(
                {'error': 'Invalid or expired state token. Please retry the authorization flow.'},
                status=403,
            )

        # Exchange authorization code for tokens
        auth_header = base64.b64encode(
            f'{creds.client_id}:{creds.client_key}'.encode()
        ).decode()

        redirect_uri = creds.redirect_uri or request.build_absolute_uri(request.path)

        try:
            token_response = requests.post(
                INTUIT_TOKEN_URL,
                headers={
                    'Authorization': f'Basic {auth_header}',
                    'Accept': 'application/json',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': redirect_uri,
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            return Response(
                {'error': f'Token exchange request failed: {exc}'},
                status=502,
            )

        if token_response.status_code >= 400:
            return Response(
                {'error': f'Token exchange failed: {token_response.text[:500]}'},
                status=502,
            )

        data = token_response.json()

        with transaction.atomic():
            creds.access_token = data.get('access_token', '')
            creds.refresh_token = data.get('refresh_token', '')
            creds.token_expiry = timezone.now() + timedelta(
                seconds=data.get('expires_in', 3600)
            )
            if realm_id:
                creds.realm_id = realm_id
            creds.redirect_uri = redirect_uri
            creds.oauth_state = ''  # Consume the nonce
            creds.save()

        # Return an HTML page that signals the opener window via postMessage
        # and then closes itself, so the parent UI can auto-refresh.
        html = '''
        <!DOCTYPE html>
        <html>
        <head><title>QuickBooks Connected</title></head>
        <body>
            <p>QuickBooks connected successfully. This window will close automatically.</p>
            <script>
                if (window.opener) {
                    window.opener.postMessage(
                        { type: 'quickbooks_connected', success: true },
                        window.location.origin
                    );
                    window.close();
                } else {
                    window.location.href = '/integrations?quickbooks_connected=true';
                }
            </script>
        </body>
        </html>
        '''
        return HttpResponse(html, content_type='text/html')


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
    permission_classes = [IsAuthenticated, IsAdminOrOwner]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['warehouse', 'type', 'status', 'market']
    search_fields = ['name', 'type', 'market']
    ordering_fields = ['created_at', 'status', 'market']
    ordering = ['-created_at']

    def perform_update(self, serializer):
        instance: Integration = serializer.instance  # type: ignore[assignment]
        new_status = serializer.validated_data.get('status')

        # Guard: cannot activate an integration unless test-connection passes
        if (
            new_status == Integration.IntegrationStatus.ACTIVE
            and instance.status != Integration.IntegrationStatus.ACTIVE
        ):
            ok, message = test_integration_connection(instance)
            if not ok:
                from rest_framework.exceptions import ValidationError
                raise ValidationError({
                    'status': f'Cannot activate: connection test failed — {message}'
                })

        serializer.save()

    @action(detail=True, methods=['post'], url_path='sync')
    def sync(self, request, pk=None):
        integration = self.get_object()

        if integration.type == Integration.IntegrationType.SHOPIFY:
            # Accept ISO datetime or YYYY-MM-DD.
            raw_from = request.data.get('date_from')
            raw_to = request.data.get('date_to')

            def _parse_or_fallback(value, *, end_of_day, fallback):
                if not value:
                    return fallback
                parsed = parse_business_datetime_filter_value(str(value), end_of_day=end_of_day)
                return parsed if isinstance(parsed, datetime) else fallback

            date_to = _parse_or_fallback(
                raw_to,
                end_of_day=True,
                fallback=timezone.now(),
            )
            date_from = _parse_or_fallback(
                raw_from,
                end_of_day=False,
                fallback=date_to - timedelta(days=1),
            )

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

    @action(detail=True, methods=['post'], url_path='quickbooks-auth-url')
    def quickbooks_auth_url(self, request, pk=None):
        """Generate the QuickBooks OAuth 2.0 authorization URL."""
        integration = self.get_object()
        if integration.type != Integration.IntegrationType.QUICKBOOKS:
            return Response(
                {'detail': 'This action is only for QuickBooks integrations.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        creds = getattr(integration, 'quickbooks_credentials', None)
        if not creds or not creds.client_id:
            return Response(
                {'detail': 'QuickBooks credentials (client_id) not configured.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        redirect_uri = request.build_absolute_uri('/api/integrations/quickbooks/callback/')

        # Generate and persist a cryptographic CSRF nonce
        csrf_token = secrets.token_urlsafe(32)
        creds.redirect_uri = redirect_uri
        creds.oauth_state = csrf_token
        creds.save(update_fields=['redirect_uri', 'oauth_state'])

        params = urlencode({
            'client_id': creds.client_id,
            'scope': 'com.intuit.quickbooks.accounting',
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'state': f'{integration.id}:{csrf_token}',
        })
        auth_url = f'{INTUIT_AUTH_URL}?{params}'

        return Response({
            'auth_url': auth_url,
            'integration_id': integration.id,
        })

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
        # Read and cache the raw body FIRST, before DRF or any middleware can
        # consume the WSGI input stream.  This must be the very first line.
        raw_body: bytes = request.body

        configured_topic = kwargs.get('topic') or self.topic
        webhook_topic = request.headers.get('X-Shopify-Topic') or configured_topic
        shop_domain = request.headers.get('X-Shopify-Shop-Domain', '').strip()
        # Strip to guard against invisible whitespace from proxies/load-balancers.
        hmac_header = request.headers.get('X-Shopify-Hmac-Sha256', '').strip()
        webhook_id = (request.headers.get('X-Shopify-Webhook-Id') or '').strip()

        if not shop_domain:
            logger.warning('Shopify webhook received without X-Shopify-Shop-Domain header.')
            return Response(
                {'detail': 'Missing Shopify shop domain header.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        integration = resolve_shopify_integration_by_shop_domain(shop_domain)
        if not integration:
            logger.warning(
                'Shopify webhook received for unknown shop domain: %s', shop_domain
            )
            return Response(
                {'detail': 'No Shopify integration found for this shop domain.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        creds = getattr(integration, 'shopify_credentials', None)
        secret = (getattr(creds, 'api_secret', '') or '').strip()

        if not secret:
            logger.error(
                'Shopify webhook received for shop %s (integration id=%s) but '
                'api_secret is not configured. Update the integration credentials '
                'with the Shopify Client/Webhook Signing Secret.',
                shop_domain,
                integration.id,
            )
            return Response(
                {'detail': 'Shopify API secret is not configured for this integration.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not verify_shopify_webhook_hmac(raw_body, hmac_header, secret):
            logger.warning(
                'Shopify webhook HMAC verification failed for shop %s, topic %s '
                '(body_len=%d, hmac_header_present=%s). '
                'Ensure api_secret matches the Shopify Webhooks Signing Secret '
                '(Admin → Settings → Notifications → Webhooks) or the app\'s '
                'Client Secret (Partner Dashboard → App → Client credentials).',
                shop_domain,
                webhook_topic,
                len(raw_body),
                bool(hmac_header),
            )
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
            logger.warning(
                'Shopify webhook processing error for shop %s, topic %s: %s',
                shop_domain, webhook_topic, exc,
            )
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'status': 'accepted', **result}, status=status.HTTP_200_OK)


class ProductMarketMappingViewSet(viewsets.ModelViewSet):
    """CRUD for per-market product ID mappings (Odoo & QuickBooks)."""

    queryset = (
        ProductMarketMapping.objects
        .select_related('product', 'market')
        .order_by('product__name', 'market__name')
    )
    serializer_class = ProductMarketMappingSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['product', 'market']
    search_fields = ['product__name', 'product__sku', 'market__name']
    ordering_fields = ['product__name', 'market__name', 'updated_at']

    @action(detail=False, methods=['post'], url_path='bulk-upsert')
    def bulk_upsert(self, request):
        """Accepts a list of {product, market, odoo_product_id, quickbooks_product_id}
        and creates/updates mappings in bulk."""
        items = request.data if isinstance(request.data, list) else []
        if not items:
            return Response(
                {'detail': 'Expected a non-empty list.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = []
        for item in items:
            product_id = item.get('product')
            market_id = item.get('market')
            if not product_id or not market_id:
                continue
            mapping, _ = ProductMarketMapping.objects.update_or_create(
                product_id=product_id,
                market_id=market_id,
                defaults={
                    'odoo_product_id': item.get('odoo_product_id', ''),
                    'quickbooks_product_id': item.get('quickbooks_product_id', ''),
                },
            )
            results.append(ProductMarketMappingSerializer(mapping).data)

        return Response(results, status=status.HTTP_200_OK)


class OrderSyncLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Read-only log of order sync attempts to Odoo and QuickBooks."""

    queryset = (
        OrderSyncLog.objects
        .select_related('order', 'integration')
        .order_by('-created_at')
    )
    serializer_class = OrderSyncLogSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['order', 'integration', 'target', 'status']
    search_fields = ['order__order_number', 'integration__name', 'error_message']
    ordering_fields = ['created_at', 'status']
