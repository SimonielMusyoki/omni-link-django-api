# pyright: reportMissingTypeArgument=false, reportIncompatibleVariableOverride=false, reportMissingParameterType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
from datetime import timedelta

from rest_framework import serializers

from .models import (
    Integration,
    ShopifyCredentials,
    OdooCredentials,
    QuickBooksCredentials,
    OrderSyncLog,
    ProductIntegrationMapping,
)


REQUIRED_CREDENTIAL_FIELDS = {
    Integration.IntegrationType.SHOPIFY: {'store_url', 'access_token', 'api_key', 'api_secret'},
    Integration.IntegrationType.ODOO: {
        'server_url',
        'database_url',
        'company_id',
        'email',
        'api_key',
        'sukhiba_partner_id',
        'pos_partner_id',
        'ecommerce_partner_id',
    },
    Integration.IntegrationType.QUICKBOOKS: {
        'realm_id',
        'client_id',
        'client_key',
    },
}


class IntegrationSerializer(serializers.ModelSerializer):
    """Serializer that adapts required credential inputs by integration type."""

    warehouse_name = serializers.CharField(source='warehouse.name', read_only=True)
    credentials = serializers.DictField(
        child=serializers.CharField(),
        write_only=True,
        required=False,
        help_text='Type-specific credentials payload.',
    )
    credential_schema = serializers.SerializerMethodField(read_only=True)
    credential_summary = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Integration
        fields = [
            'id',
            'name',
            'type',
            'market',
            'status',
            'warehouse',
            'warehouse_name',
            'auto_sync_orders',
            'credentials',
            'credential_schema',
            'credential_summary',
            'created_at',
            'updated_at',
            'last_sync',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'last_sync']

    def get_credential_schema(self, obj):
        return sorted(REQUIRED_CREDENTIAL_FIELDS.get(obj.type, []))

    def get_credential_summary(self, obj):
        if obj.type == Integration.IntegrationType.SHOPIFY:
            creds = getattr(obj, 'shopify_credentials', None)
            if not creds:
                return None
            return {
                'store_url': creds.store_url,
                'api_version': creds.api_version,
                'has_api_key': bool(creds.api_key),
                'has_api_secret': bool(creds.api_secret),
                'has_access_token': bool(creds.access_token),
            }

        if obj.type == Integration.IntegrationType.ODOO:
            creds = getattr(obj, 'odoo_credentials', None)
            if not creds:
                return None
            return {
                'server_url': creds.server_url,
                'database_url': creds.database_url,
                'company_id': creds.company_id,
                'email': creds.email,
                'sukhiba_partner_id': creds.sukhiba_partner_id,
                'pos_partner_id': creds.pos_partner_id,
                'ecommerce_partner_id': creds.ecommerce_partner_id,
                'tax_id': creds.tax_id,
                'shipping_fee_account_id': creds.shipping_fee_account_id,
                'default_product_id': creds.default_product_id,
                'has_api_key': bool(creds.api_key),
            }

        if obj.type == Integration.IntegrationType.QUICKBOOKS:
            creds = getattr(obj, 'quickbooks_credentials', None)
            if not creds:
                return None
            from django.utils import timezone as _tz
            is_connected = bool(
                creds.access_token
                and creds.token_expiry
                and _tz.now() < creds.token_expiry - timedelta(minutes=5)
            )
            return {
                'realm_id': creds.realm_id,
                'client_id': creds.client_id,
                'sukhiba_customer_id': creds.sukhiba_customer_id,
                'pos_customer_id': creds.pos_customer_id,
                'ecommerce_customer_id': creds.ecommerce_customer_id,
                'tax_id': creds.tax_id,
                'shipping_fee_account_id': creds.shipping_fee_account_id,
                'default_product_id': creds.default_product_id,
                'environment': creds.environment,
                'invoice_prefix': creds.invoice_prefix,
                'has_client_key': bool(creds.client_key),
                'has_access_token': bool(creds.access_token),
                'is_connected': is_connected,
            }

        return None

    def validate(self, attrs):
        integration_type = attrs.get('type', getattr(self.instance, 'type', None))
        credentials = attrs.get('credentials')
        credentials_required = self.instance is None or 'type' in attrs or 'credentials' in attrs

        if credentials_required and credentials is None:
            raise serializers.ValidationError({'credentials': 'Credentials are required.'})

        if credentials is None:
            return attrs

        required_fields = REQUIRED_CREDENTIAL_FIELDS.get(integration_type, set())
        existing_credentials = self._existing_credentials_map(
            self.instance,
            integration_type,
        )
        merged_credentials = {
            **existing_credentials,
            **{
                key: value
                for key, value in credentials.items()
                if str(value).strip()
            },
        }
        missing_fields = sorted(
            field
            for field in required_fields
            if not str(merged_credentials.get(field, '')).strip()
        )
        if missing_fields:
            raise serializers.ValidationError({
                'credentials': f"Missing required fields for {integration_type}: {', '.join(missing_fields)}"
            })

        return attrs

    def create(self, validated_data):
        credentials = validated_data.pop('credentials', None)
        integration = Integration.objects.create(**validated_data)
        if credentials:
            self._upsert_credentials(integration, integration.type, credentials)
        return integration

    def update(self, instance, validated_data):
        credentials = validated_data.pop('credentials', None)
        original_type = instance.type

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if original_type != instance.type:
            self._clear_credentials(instance)

        if credentials is not None:
            self._upsert_credentials(instance, instance.type, credentials)

        return instance

    def _clear_credentials(self, integration):
        ShopifyCredentials.objects.filter(integration=integration).delete()
        OdooCredentials.objects.filter(integration=integration).delete()
        QuickBooksCredentials.objects.filter(integration=integration).delete()

    def _upsert_credentials(self, integration, integration_type, credentials):
        existing = self._existing_credentials_map(integration, integration_type)
        merged = {
            **existing,
            **{
                key: value
                for key, value in credentials.items()
                if str(value).strip()
            },
        }

        if integration_type == Integration.IntegrationType.SHOPIFY:
            creds, _ = ShopifyCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'store_url': merged['store_url'],
                    'access_token': merged['access_token'],
                    'api_key': merged['api_key'],
                    'api_secret': merged['api_secret'],
                    'api_version': merged.get('api_version', '2024-01'),
                },
            )
            integration.shopify_credentials = creds
            return

        if integration_type == Integration.IntegrationType.ODOO:
            creds, _ = OdooCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'server_url': merged['server_url'],
                    'database_url': merged['database_url'],
                    'company_id': merged['company_id'],
                    'email': merged['email'],
                    'api_key': merged['api_key'],
                    'sukhiba_partner_id': merged['sukhiba_partner_id'],
                    'pos_partner_id': merged['pos_partner_id'],
                    'ecommerce_partner_id': merged['ecommerce_partner_id'],
                    'tax_id': merged.get('tax_id', ''),
                    'shipping_fee_account_id': merged.get('shipping_fee_account_id', ''),
                    'default_product_id': merged.get('default_product_id', ''),
                },
            )
            integration.odoo_credentials = creds
            return

        if integration_type == Integration.IntegrationType.QUICKBOOKS:
            creds, _ = QuickBooksCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'realm_id': merged['realm_id'],
                    'client_id': merged['client_id'],
                    'client_key': merged['client_key'],
                    'sukhiba_customer_id': merged.get('sukhiba_customer_id', ''),
                    'pos_customer_id': merged.get('pos_customer_id', ''),
                    'ecommerce_customer_id': merged.get('ecommerce_customer_id', ''),
                    'tax_id': merged.get('tax_id', ''),
                    'shipping_fee_account_id': merged.get('shipping_fee_account_id', ''),
                    'default_product_id': merged.get('default_product_id', ''),
                    'environment': merged.get('environment', 'SANDBOX'),
                    'invoice_prefix': merged.get('invoice_prefix', ''),
                },
            )
            integration.quickbooks_credentials = creds
            return

        raise serializers.ValidationError({'type': 'Unsupported integration type.'})

    def _existing_credentials_map(self, instance, integration_type):
        """Return existing credential values for merge-on-update.

        ⚠️ INTERNAL ONLY — contains sensitive fields (api_key, tokens).
        Never expose this dict in API responses; use get_credential_summary instead.
        """
        if instance is None:
            return {}

        if integration_type == Integration.IntegrationType.SHOPIFY:
            creds = getattr(instance, 'shopify_credentials', None)
            if not creds:
                return {}
            return {
                'store_url': creds.store_url,
                'access_token': creds.access_token,
                'api_key': creds.api_key,
                'api_secret': creds.api_secret,
                'api_version': creds.api_version,
            }

        if integration_type == Integration.IntegrationType.ODOO:
            creds = getattr(instance, 'odoo_credentials', None)
            if not creds:
                return {}
            return {
                'server_url': creds.server_url,
                'database_url': creds.database_url,
                'company_id': creds.company_id,
                'email': creds.email,
                'api_key': creds.api_key,
                'sukhiba_partner_id': creds.sukhiba_partner_id,
                'pos_partner_id': creds.pos_partner_id,
                'ecommerce_partner_id': creds.ecommerce_partner_id,
                'tax_id': creds.tax_id,
                'shipping_fee_account_id': creds.shipping_fee_account_id,
                'default_product_id': creds.default_product_id,
            }

        if integration_type == Integration.IntegrationType.QUICKBOOKS:
            creds = getattr(instance, 'quickbooks_credentials', None)
            if not creds:
                return {}
            return {
                'realm_id': creds.realm_id,
                'client_id': creds.client_id,
                'client_key': creds.client_key,
                'sukhiba_customer_id': creds.sukhiba_customer_id,
                'pos_customer_id': creds.pos_customer_id,
                'ecommerce_customer_id': creds.ecommerce_customer_id,
                'tax_id': creds.tax_id,
                'shipping_fee_account_id': creds.shipping_fee_account_id,
                'default_product_id': creds.default_product_id,
                'environment': creds.environment,
                'invoice_prefix': creds.invoice_prefix,
                # OAuth tokens are backend-managed; never expose them in API responses
            }

        return {}






class OrderSyncLogSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    integration_name = serializers.CharField(source='integration.name', read_only=True)
    target_display = serializers.CharField(source='get_target_display', read_only=True)

    class Meta:
        model = OrderSyncLog
        fields = [
            'id', 'order', 'order_number',
            'integration', 'integration_name',
            'target', 'target_display',
            'status', 'external_id', 'error_message',
            'request_payload',
            'created_at',
        ]
        read_only_fields = fields

class ProductIntegrationMappingSerializer(serializers.ModelSerializer):
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)
    integration_name = serializers.CharField(source='integration.name', read_only=True)
    integration_type = serializers.CharField(source='integration.type', read_only=True)
    market_name = serializers.CharField(source='integration.market.name', read_only=True)

    class Meta:
        model = ProductIntegrationMapping
        fields = [
            'id', 'product', 'product_sku', 'product_name',
            'integration', 'integration_name', 'integration_type', 'market_name',
            'external_sku', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
