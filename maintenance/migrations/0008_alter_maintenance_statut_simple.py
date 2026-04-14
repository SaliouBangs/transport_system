from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0007_maintenance_prochaine_vidange_dans_km"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenance",
            name="statut",
            field=models.CharField(
                choices=[
                    ("en_cours", "En cours"),
                    ("terminee", "Terminee"),
                ],
                default="en_cours",
                max_length=20,
            ),
        ),
        migrations.RunSQL(
            """
            UPDATE maintenance_maintenance
            SET statut = 'en_cours'
            WHERE statut NOT IN ('en_cours', 'terminee');
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
