from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShopifyWebhookDelivery',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('webhook_id', models.CharField(max_length=255, unique=True)),
                ('topic', models.CharField(max_length=255)),
                ('shop_domain', models.CharField(max_length=255)),
                ('status', models.CharField(choices=[('RECEIVED', 'Received'), ('PROCESSED', 'Processed'), ('FAILED', 'Failed')], default='RECEIVED', max_length=20)),
                ('error_message', models.TextField(blank=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['topic', 'shop_domain'], name='integration_topic_7b91ce_idx'), models.Index(fields=['status', '-created_at'], name='integration_status_556497_idx')],
            },
        ),
    ]

