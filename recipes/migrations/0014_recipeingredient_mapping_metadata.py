from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0013_alter_recipesource_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipeingredient",
            name="match_embedding_model",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="recipeingredient",
            name="match_method",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="recipeingredient",
            name="match_policy_version",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="recipeingredient",
            name="match_score",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
