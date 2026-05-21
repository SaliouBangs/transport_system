from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("maintenance", "0028_approvisionnementcaisse"),
    ]

    operations = [
        migrations.AddField(
            model_name="approvisionnementcaisse",
            name="banque_cheque",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="approvisionnementcaisse",
            name="date_cheque",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="approvisionnementcaisse",
            name="mode_approvisionnement",
            field=models.CharField(choices=[("espece", "Espece"), ("cheque", "Cheque")], default="espece", max_length=20),
        ),
        migrations.AddField(
            model_name="approvisionnementcaisse",
            name="nature_approvisionnement",
            field=models.CharField(choices=[("urgence_dg_espece", "Avance DG en espece"), ("retrait_remboursement", "Retrait cheque pour remboursement"), ("cheque_direct", "Approvisionnement par cheque")], default="urgence_dg_espece", max_length=40),
        ),
        migrations.AddField(
            model_name="approvisionnementcaisse",
            name="reference_cheque",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
