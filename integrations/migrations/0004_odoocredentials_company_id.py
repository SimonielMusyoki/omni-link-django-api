from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('integrations', '0003_rename_integration_topic_7b91ce_idx_integration_topic_1f4b60_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='odoocredentials',
            name='company_id',
            field=models.CharField(default='default-company', max_length=255),
            preserve_default=False,
        ),
    ]

