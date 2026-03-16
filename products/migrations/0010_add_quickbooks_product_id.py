from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0009_add_odoo_product_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='quickbooks_product_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='QuickBooks item ID for sync',
                max_length=255,
            ),
        ),
    ]
