from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0006_maintenance_kilometrage_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenance",
            name="prochaine_vidange_dans_km",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
