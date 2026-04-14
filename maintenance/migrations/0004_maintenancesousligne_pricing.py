from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0003_maintenancesousligne"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancesousligne",
            name="quantite",
            field=models.DecimalField(decimal_places=2, default=1, max_digits=10),
        ),
        migrations.AddField(
            model_name="maintenancesousligne",
            name="prix_unitaire",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="maintenancesousligne",
            name="montant",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
