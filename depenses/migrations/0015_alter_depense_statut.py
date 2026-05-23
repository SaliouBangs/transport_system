from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0014_depense_entite_depense"),
    ]

    operations = [
        migrations.AlterField(
            model_name="depense",
            name="statut",
            field=models.CharField(
                choices=[
                    ("brouillon", "Brouillon"),
                    ("attente_validation_expression", "En attente validation expression DGA SOGEFI"),
                    ("attente_validation_expression_dg", "En attente validation expression DG"),
                    ("rejetee_expression", "Expression rejetee"),
                    ("attente_validation_chargement_dga", "En attente validation depense chargement DGA"),
                    ("attente_validation_chargement_dg", "En attente validation depense chargement DG"),
                    ("rejetee_chargement", "Depense de chargement rejetee"),
                    ("attente_engagement_achat", "En attente de saisie achat / prix"),
                    ("attente_validation_dga_engagement", "En attente validation DGA"),
                    ("attente_validation_dg_engagement", "En attente validation DG"),
                    ("rejetee_dga_engagement", "Engagement rejete par DGA"),
                    ("rejetee_dg_engagement", "Engagement rejete par DG"),
                    ("attente_paiement_comptable", "En attente traitement comptable"),
                    ("attente_paiement_caissiere", "En attente traitement caisse"),
                    ("payee", "Payee"),
                ],
                default="attente_validation_expression",
                max_length=40,
            ),
        ),
    ]
