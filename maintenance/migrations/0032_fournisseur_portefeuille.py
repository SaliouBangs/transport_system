from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0031_maintenance_banque_cheque_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="fournisseur",
            name="numero_telephone",
            field=models.CharField(blank=True, max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="fournisseur",
            name="portefeuille",
            field=models.CharField(
                choices=[("logistique", "Logistique"), ("interne", "Depenses internes")],
                default="logistique",
                max_length=20,
            ),
        ),
    ]
