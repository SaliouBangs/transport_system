from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("camions", "0004_alter_camion_type_camion"),
    ]

    operations = [
        migrations.AddField(
            model_name="camion",
            name="kilometrage_actuel",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="camion",
            name="kilometrage_alerte_vidange",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="camion",
            name="kilometrage_derniere_vidange",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
