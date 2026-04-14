from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0005_alter_maintenance_dates_to_datetime"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenance",
            name="kilometrage_entree",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="maintenance",
            name="kilometrage_sortie",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
