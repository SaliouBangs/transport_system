from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0008_depense_date_bon_conso_depense_quantite_a_consommer"),
    ]

    operations = [
        migrations.AddField(
            model_name="depenseligne",
            name="commentaire",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="depenseligne",
            name="date_bon_conso",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="depenseligne",
            name="type_depense",
            field=models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name="lignes_depense", to="depenses.typedepense"),
        ),
    ]
