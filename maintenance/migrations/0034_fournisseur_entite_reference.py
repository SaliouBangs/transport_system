from django.db import migrations, models


def seed_internal_supplier_entities(apps, schema_editor):
    Fournisseur = apps.get_model("maintenance", "Fournisseur")
    Fournisseur.objects.filter(portefeuille="interne", entite_reference="").update(entite_reference="sogefi")


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0033_typemaintenance_actif"),
    ]

    operations = [
        migrations.AddField(
            model_name="fournisseur",
            name="entite_reference",
            field=models.CharField(blank=True, choices=[("sogefi", "SOGEFI"), ("soni", "SONI"), ("avena", "Avena")], max_length=20),
        ),
        migrations.RunPython(seed_internal_supplier_entities, migrations.RunPython.noop),
    ]
