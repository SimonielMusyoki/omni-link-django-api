from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('products', '0003_productbundle_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='Integration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('type', models.CharField(choices=[('SHOPIFY', 'Shopify'), ('ODOO', 'Odoo'), ('QUICKBOOKS', 'QuickBooks')], max_length=20)),
                ('market', models.CharField(help_text='Country/market name, e.g. Nigeria.', max_length=100)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('INACTIVE', 'Inactive'), ('ERROR', 'Error')], default='INACTIVE', max_length=20)),
                ('last_sync', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('warehouse', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='integrations', to='products.warehouse')),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['type', 'market'], name='integration_type_b44f1f_idx'), models.Index(fields=['status'], name='integration_status_86f514_idx')],
                'constraints': [models.UniqueConstraint(fields=('type', 'market'), name='unique_integration_type_per_market')],
            },
        ),
        migrations.CreateModel(
            name='OdooCredentials',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('server_url', models.URLField()),
                ('database_url', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('api_key', models.CharField(max_length=512)),
                ('integration', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='odoo_credentials', to='integrations.integration')),
            ],
        ),
        migrations.CreateModel(
            name='QuickBooksCredentials',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('realm_id', models.CharField(max_length=255)),
                ('client_id', models.CharField(max_length=255)),
                ('client_key', models.CharField(max_length=512)),
                ('environment', models.CharField(choices=[('SANDBOX', 'Sandbox'), ('PRODUCTION', 'Production')], default='SANDBOX', max_length=20)),
                ('integration', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='quickbooks_credentials', to='integrations.integration')),
            ],
        ),
        migrations.CreateModel(
            name='ShopifyCredentials',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('store_url', models.URLField()),
                ('access_token', models.CharField(max_length=512)),
                ('api_version', models.CharField(default='2024-01', max_length=20)),
                ('integration', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='shopify_credentials', to='integrations.integration')),
            ],
        ),
    ]
