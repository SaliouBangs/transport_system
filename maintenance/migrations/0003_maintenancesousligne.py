import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0002_rework_maintenance_facturation"),
    ]

    operations = [
        migrations.CreateModel(
            name="MaintenanceSousLigne",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("libelle", models.CharField(max_length=200)),
                (
                    "maintenance_ligne",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sous_lignes",
                        to="maintenance.maintenanceligne",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
    ]
