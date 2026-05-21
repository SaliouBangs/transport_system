from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0032_fournisseur_portefeuille"),
    ]

    operations = [
        migrations.AddField(
            model_name="typemaintenance",
            name="actif",
            field=models.BooleanField(default=True),
        ),
    ]
