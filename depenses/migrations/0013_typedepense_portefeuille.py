from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0012_depense_date_expression"),
    ]

    operations = [
        migrations.AlterField(
            model_name="typedepense",
            name="libelle",
            field=models.CharField(max_length=150),
        ),
        migrations.AddField(
            model_name="typedepense",
            name="portefeuille",
            field=models.CharField(
                choices=[("logistique", "Logistique"), ("interne", "Depenses internes")],
                default="logistique",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="typedepense",
            constraint=models.UniqueConstraint(fields=("libelle", "portefeuille"), name="unique_type_depense_par_portefeuille"),
        ),
    ]
