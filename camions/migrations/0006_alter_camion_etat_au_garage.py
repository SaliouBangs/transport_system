from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camions", "0005_camion_kilometrage_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="camion",
            name="etat",
            field=models.CharField(
                choices=[
                    ("disponible", "Disponible"),
                    ("mission", "En mission"),
                    ("au_garage", "Au garage"),
                ],
                default="disponible",
                max_length=20,
            ),
        ),
    ]
