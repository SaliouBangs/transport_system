from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


TYPE_LABELS = {
    "vidange": "Vidange",
    "revision": "Revision generale",
    "panne": "Reparation de panne",
    "pneus": "Changement de pneus",
    "autre": "Autre",
}


def migrate_maintenance_lines(apps, schema_editor):
    Maintenance = apps.get_model("maintenance", "Maintenance")
    TypeMaintenance = apps.get_model("maintenance", "TypeMaintenance")
    MaintenanceLigne = apps.get_model("maintenance", "MaintenanceLigne")

    type_cache = {}
    for maintenance in Maintenance.objects.all().order_by("id"):
        reference = f"MAIN{maintenance.id:03d}"
        Maintenance.objects.filter(pk=maintenance.pk).update(reference=reference)

        type_code = getattr(maintenance, "type_maintenance", "") or "autre"
        type_label = TYPE_LABELS.get(type_code, "Autre")
        if type_label not in type_cache:
            type_cache[type_label], _ = TypeMaintenance.objects.get_or_create(libelle=type_label)

        cout = getattr(maintenance, "cout", None)
        montant = cout if cout is not None else Decimal("0")
        libelle = (maintenance.observation or "").strip() or type_label

        MaintenanceLigne.objects.create(
            maintenance_id=maintenance.id,
            type_maintenance=type_cache[type_label],
            libelle=libelle[:200],
            quantite=Decimal("1"),
            prix_unitaire=montant,
            montant=montant,
        )
        Maintenance.objects.filter(pk=maintenance.pk).update(total_facture=montant)


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="TypeMaintenance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("libelle", models.CharField(max_length=120, unique=True)),
            ],
            options={"ordering": ["libelle"]},
        ),
        migrations.AddField(
            model_name="maintenance",
            name="reference",
            field=models.CharField(blank=True, editable=False, max_length=20, unique=True),
        ),
        migrations.AddField(
            model_name="maintenance",
            name="total_facture",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.RenameField(
            model_name="maintenance",
            old_name="description",
            new_name="observation",
        ),
        migrations.AlterField(
            model_name="maintenance",
            name="observation",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="MaintenanceLigne",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("libelle", models.CharField(max_length=200)),
                ("quantite", models.DecimalField(decimal_places=2, default=1, max_digits=10)),
                ("prix_unitaire", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("montant", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                (
                    "maintenance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lignes",
                        to="maintenance.maintenance",
                    ),
                ),
                (
                    "type_maintenance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lignes_maintenance",
                        to="maintenance.typemaintenance",
                    ),
                ),
            ],
            options={"ordering": ["id"]},
        ),
        migrations.RunPython(migrate_maintenance_lines, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="maintenance",
            name="cout",
        ),
        migrations.RemoveField(
            model_name="maintenance",
            name="type_maintenance",
        ),
    ]
