from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0013_typedepense_portefeuille"),
    ]

    operations = [
        migrations.AddField(
            model_name="depense",
            name="entite_depense",
            field=models.CharField(
                choices=[("sogefi", "SOGEFI"), ("soni", "SONI"), ("avena", "Avena")],
                default="sogefi",
                max_length=20,
            ),
        ),
    ]
