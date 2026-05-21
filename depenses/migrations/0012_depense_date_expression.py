from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("depenses", "0011_typepieceidentite_depense_numero_piece_identite_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="depense",
            name="date_expression",
            field=models.DateField(default=django.utils.timezone.localdate),
        ),
    ]
