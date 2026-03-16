from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0008_add_odoo_ids_to_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='quickbooks_sales_invoice_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='QuickBooks Sales Invoice record ID',
                max_length=255,
            ),
        ),
    ]
