from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0004_remove_legacy_integration'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_physical',
            field=models.BooleanField(
                default=True,
                help_text='Physical products track warehouse inventory; virtual products do not.',
            ),
        ),
    ]

