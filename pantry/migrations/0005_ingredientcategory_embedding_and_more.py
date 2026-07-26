from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0004_canonicalingredient_embedding_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingredientcategory",
            name="embedding",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="ingredientcategory",
            name="embedding_model",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]