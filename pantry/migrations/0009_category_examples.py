import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0008_add_operational_diagnostics"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingredientcategory",
            name="minimum_margin",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="ingredientcategory",
            name="minimum_similarity",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="IngredientCategoryExample",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("text", models.CharField(max_length=120)),
                ("normalized_text", models.CharField(max_length=120)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("starter", "Starter catalog"),
                            ("owner", "Owner"),
                            ("confirmed", "Confirmed assignment"),
                            ("imported", "Imported"),
                        ],
                        max_length=12,
                    ),
                ),
                ("source_key", models.CharField(blank=True, max_length=120)),
                ("active", models.BooleanField(default=True)),
                ("embedding", models.JSONField(blank=True, default=list)),
                ("embedding_model", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "category",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="examples",
                        to="pantry.ingredientcategory",
                    ),
                ),
                (
                    "household",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ingredient_category_examples",
                        to="core.household",
                    ),
                ),
            ],
            options={"ordering": ["category__sort_order", "category__name", "text"]},
        ),
        migrations.AddConstraint(
            model_name="ingredientcategoryexample",
            constraint=models.UniqueConstraint(
                fields=("category", "normalized_text"),
                name="unique_category_example_text",
            ),
        ),
        migrations.AddConstraint(
            model_name="ingredientcategoryexample",
            constraint=models.UniqueConstraint(
                condition=models.Q(("source_key__gt", "")),
                fields=("category", "source", "source_key"),
                name="unique_category_example_source_key",
            ),
        ),
        migrations.AddIndex(
            model_name="ingredientcategoryexample",
            index=models.Index(
                fields=["household", "category", "active"],
                name="pantry_inge_househo_7ca0b4_idx",
            ),
        ),
    ]
