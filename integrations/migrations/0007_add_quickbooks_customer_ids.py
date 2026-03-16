from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0006_add_odoo_partner_ids'),
    ]

    operations = [
        migrations.AddField(
            model_name='quickbookscredentials',
            name='ecommerce_customer_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='QuickBooks customer ID for regular e-commerce orders',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='quickbookscredentials',
            name='pos_customer_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='QuickBooks customer ID for POS orders',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='quickbookscredentials',
            name='sukhiba_customer_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='QuickBooks customer ID for orders tagged origin:sukhiba',
                max_length=255,
            ),
        ),
    ]
