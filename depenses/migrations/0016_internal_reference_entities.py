from django.db import migrations, models


def seed_internal_reference_entities(apps, schema_editor):
    TypeDepense = apps.get_model("depenses", "TypeDepense")
    LieuProjet = apps.get_model("depenses", "LieuProjet")

    TypeDepense.objects.filter(portefeuille="interne", entite_reference="").update(entite_reference="sogefi")
    LieuProjet.objects.filter().update(entite_reference="sogefi")


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0015_alter_depense_statut"),
    ]

    operations = [
        migrations.AddField(
            model_name="typedepense",
            name="entite_reference",
            field=models.CharField(blank=True, choices=[("sogefi", "SOGEFI"), ("soni", "SONI"), ("avena", "Avena")], max_length=20),
        ),
        migrations.AddField(
            model_name="lieuprojet",
            name="entite_reference",
            field=models.CharField(choices=[("sogefi", "SOGEFI"), ("soni", "SONI"), ("avena", "Avena")], default="sogefi", max_length=20),
        ),
        migrations.RunPython(seed_internal_reference_entities, migrations.RunPython.noop),
    ]
