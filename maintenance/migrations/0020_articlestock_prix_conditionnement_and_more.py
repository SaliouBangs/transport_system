from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0019_alertefactureresolue"),
    ]

    operations = [
        migrations.AddField(
            model_name="articlestock",
            name="prix_conditionnement",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="articlestock",
            name="unite_prix_conditionnement",
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
