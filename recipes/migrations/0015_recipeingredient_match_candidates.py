from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0014_recipeingredient_mapping_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipeingredient",
            name="match_candidates",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
