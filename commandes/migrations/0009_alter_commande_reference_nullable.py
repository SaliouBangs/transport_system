from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("commandes", "0008_commande_delai_paiement_jours_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="commande",
            name="reference",
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
    ]
