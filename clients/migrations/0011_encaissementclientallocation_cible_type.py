from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("clients", "0010_banque_alter_encaissementclient_type_encaissement_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="encaissementclientallocation",
            name="cible_type",
            field=models.CharField(
                choices=[
                    ("commande", "Commande"),
                    ("solde_initial", "Solde initial"),
                    ("avance_client", "Avance client"),
                ],
                default="commande",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="encaissementclientallocation",
            name="commande",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.CASCADE,
                related_name="encaissement_allocations",
                to="commandes.commande",
            ),
        ),
    ]
