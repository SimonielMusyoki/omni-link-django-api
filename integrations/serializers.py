from rest_framework import serializers

from .models import (
    Integration,
    ShopifyCredentials,
    OdooCredentials,
    QuickBooksCredentials,
)


REQUIRED_CREDENTIAL_FIELDS = {
    Integration.IntegrationType.SHOPIFY: {'store_url', 'access_token', 'api_key', 'api_secret'},
    Integration.IntegrationType.ODOO: {
        'server_url',
        'database_url',
        'company_id',
        'email',
        'api_key',
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
                'has_api_key': bool(creds.api_key),
            }

        if obj.type == Integration.IntegrationType.QUICKBOOKS:
            creds = getattr(obj, 'quickbooks_credentials', None)
            if not creds:
                return None
            return {
                'realm_id': creds.realm_id,
                'client_id': creds.client_id,
                'environment': creds.environment,
                'has_client_key': bool(creds.client_key),
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
        missing_fields = sorted(
            field
            for field in required_fields
            if not str(credentials.get(field, '')).strip()
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
        if integration_type == Integration.IntegrationType.SHOPIFY:
            ShopifyCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'store_url': credentials['store_url'],
                    'access_token': credentials['access_token'],
                    'api_key': credentials['api_key'],
                    'api_secret': credentials['api_secret'],
                    'api_version': credentials.get('api_version', '2024-01'),
                },
            )
            return

        if integration_type == Integration.IntegrationType.ODOO:
            OdooCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'server_url': credentials['server_url'],
                    'database_url': credentials['database_url'],
                    'company_id': credentials['company_id'],
                    'email': credentials['email'],
                    'api_key': credentials['api_key'],
                },
            )
            return

        if integration_type == Integration.IntegrationType.QUICKBOOKS:
            QuickBooksCredentials.objects.update_or_create(
                integration=integration,
                defaults={
                    'realm_id': credentials['realm_id'],
                    'client_id': credentials['client_id'],
                    'client_key': credentials['client_key'],
                    'environment': credentials.get('environment', 'SANDBOX'),
                },
            )
            return

        raise serializers.ValidationError({'type': 'Unsupported integration type.'})
