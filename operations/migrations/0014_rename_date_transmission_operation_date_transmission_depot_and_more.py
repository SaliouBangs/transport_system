from django.db import migrations, models


def copy_transmission_to_declaration(apps, schema_editor):
    Operation = apps.get_model("operations", "Operation")
    for operation in Operation.objects.all():
        if (
            not operation.date_bons_declares
            and operation.date_transmission_depot
            and operation.etat_bon in {"declare", "liquide", "charge", "livre"}
        ):
            operation.date_bons_declares = operation.date_transmission_depot
            operation.save(update_fields=["date_bons_declares"])


class Migration(migrations.Migration):

    dependencies = [
        ("operations", "0013_operation_quantite_livree"),
    ]

    operations = [
        migrations.RenameField(
            model_name="operation",
            old_name="date_transmission",
            new_name="date_transmission_depot",
        ),
        migrations.AddField(
            model_name="operation",
            name="date_bons_declares",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.RunPython(copy_transmission_to_declaration, migrations.RunPython.noop),
    ]
