from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0029_approvisionnementcaisse_modes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SoldeInitialCaisse",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("montant_initial", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("date_reference", models.DateField(default=django.utils.timezone.localdate)),
                ("observation", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("caissiere", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="solde_initial_caisse", to=settings.AUTH_USER_MODEL)),
                ("saisi_par", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="soldes_initiaux_caisse_saisis", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["caissiere__username"],
            },
        ),
    ]
