from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0004_maintenancesousligne_pricing"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenance",
            name="date_debut",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="maintenance",
            name="date_fin",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
