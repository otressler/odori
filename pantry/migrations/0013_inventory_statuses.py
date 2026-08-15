from django.db import migrations, models


def migrate_inventory_statuses(apps, schema_editor):
    InventoryItem = apps.get_model("pantry", "InventoryItem")
    InventoryEvent = apps.get_model("pantry", "InventoryEvent")
    InventoryItem.objects.filter(status="in_stock").update(status="available")
    InventoryItem.objects.filter(status="needs_replenishment").update(status="unavailable")
    InventoryEvent.objects.filter(previous_status="in_stock").update(previous_status="available")
    InventoryEvent.objects.filter(previous_status="needs_replenishment").update(
        previous_status="unavailable"
    )
    InventoryEvent.objects.filter(new_status="in_stock").update(new_status="available")
    InventoryEvent.objects.filter(new_status="needs_replenishment").update(
        new_status="unavailable"
    )


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0012_ingredienticonjob_available_at_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inventoryitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("available", "Vorrätig"),
                    ("unknown", "Unbekannt"),
                    ("unavailable", "Nicht verfügbar"),
                ],
                default="unknown",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="inventoryevent",
            name="previous_status",
            field=models.CharField(
                choices=[
                    ("available", "Vorrätig"),
                    ("unknown", "Unbekannt"),
                    ("unavailable", "Nicht verfügbar"),
                ],
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="inventoryevent",
            name="new_status",
            field=models.CharField(
                choices=[
                    ("available", "Vorrätig"),
                    ("unknown", "Unbekannt"),
                    ("unavailable", "Nicht verfügbar"),
                ],
                max_length=24,
            ),
        ),
        migrations.RunPython(migrate_inventory_statuses, migrations.RunPython.noop),
    ]
