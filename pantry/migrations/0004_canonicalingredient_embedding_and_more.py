# Generated manually to preserve forward migration compatibility.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0003_alter_inventoryevent_new_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="canonicalingredient",
            name="embedding",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="canonicalingredient",
            name="embedding_model",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]