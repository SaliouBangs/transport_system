from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0020_articlestock_prix_conditionnement_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="mouvementstock",
            name="fournisseur",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="mouvements_stock", to="maintenance.fournisseur"),
        ),
        migrations.AddField(
            model_name="mouvementstock",
            name="montant_net",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="mouvementstock",
            name="prix_conditionnement",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="mouvementstock",
            name="remise",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
    ]
