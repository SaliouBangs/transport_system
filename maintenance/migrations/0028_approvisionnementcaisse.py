from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0027_maintenance_paiement_saisi_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ApprovisionnementCaisse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(blank=True, editable=False, max_length=20, unique=True)),
                ("date_approvisionnement", models.DateField(default=django.utils.timezone.localdate)),
                ("montant", models.DecimalField(decimal_places=2, max_digits=12)),
                ("observation", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("caissiere", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="approvisionnements_caisse_recus", to=settings.AUTH_USER_MODEL)),
                ("saisi_par", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approvisionnements_caisse_saisis", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["-date_approvisionnement", "-id"],
            },
        ),
    ]
