from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('products', '0003_productbundle_and_more'),
    ]

    operations = [
        migrations.DeleteModel(
            name='Integration',
        ),
    ]

