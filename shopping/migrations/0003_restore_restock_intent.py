from django.db import migrations


def restore_restock_intent(apps, schema_editor):
    InventoryItem = apps.get_model("pantry", "InventoryItem")
    ShoppingList = apps.get_model("shopping", "ShoppingList")
    ShoppingItem = apps.get_model("shopping", "ShoppingItem")

    legacy_items = InventoryItem.objects.filter(status="unavailable").select_related(
        "household", "ingredient"
    )
    for inventory_item in legacy_items:
        if ShoppingItem.objects.filter(
            shopping_list__household=inventory_item.household,
            canonical_ingredient=inventory_item.ingredient,
            state="open",
        ).exists():
            continue
        shopping_list = (
            ShoppingList.objects.filter(
                household=inventory_item.household,
                state="active",
            )
            .order_by("-created_at")
            .first()
        )
        if shopping_list is None:
            shopping_list = ShoppingList.objects.create(
                household=inventory_item.household,
                name="Einkaufsliste",
            )
        ShoppingItem.objects.create(
            shopping_list=shopping_list,
            canonical_ingredient=inventory_item.ingredient,
            label=inventory_item.ingredient.name,
            grouping_key=f"pantry:{inventory_item.ingredient_id}",
            source="manual",
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0013_inventory_statuses"),
        ("shopping", "0002_shoppingitem_shopping_item_list_state_idx"),
    ]

    operations = [
        migrations.RunPython(restore_restock_intent, migrations.RunPython.noop),
    ]
