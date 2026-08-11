import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recipes", "0009_queue_generated_recipe_requests")]
    operations = [
        migrations.CreateModel(
            name="ImportSource",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("source_type", models.CharField(choices=[("url", "URL"), ("image", "Image"), ("pdf", "PDF")], max_length=10)),
                ("url", models.URLField(blank=True, max_length=2048)),
                ("file", models.FileField(blank=True, upload_to="recipe-imports/")),
                ("content_hash", models.CharField(max_length=64)),
                ("content_length", models.PositiveBigIntegerField(default=0)),
                ("content_type", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("household", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="import_sources", to="core.household")),
            ],
        ),
        migrations.CreateModel(
            name="RecipeImportJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("state", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed"), ("cancelled", "Cancelled")], default="queued", max_length=12)),
                ("stage", models.CharField(choices=[("acquire", "Acquire"), ("extract", "Extract"), ("normalize", "Normalize"), ("review", "Review")], default="acquire", max_length=12)),
                ("attempt_count", models.PositiveIntegerField(default=0)), ("max_attempts", models.PositiveIntegerField(default=3)),
                ("available_at", models.DateTimeField(blank=True, null=True)), ("lease_id", models.UUIDField(blank=True, null=True)), ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("correlation_id", models.UUIDField(default=uuid.uuid4, editable=False)), ("error_code", models.CharField(blank=True, max_length=80)), ("error_message", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("started_at", models.DateTimeField(blank=True, null=True)), ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("household", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recipe_import_jobs", to="core.household")),
                ("recipe", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="import_jobs", to="recipes.recipe")),
                ("source", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="jobs", to="recipes.importsource")),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(fields=["state", "available_at"], name="recipe_import_ready_idx"),
                    models.Index(fields=["lease_expires_at"], name="recipe_import_lease_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["source"],
                        condition=models.Q(state__in=["queued", "running"]),
                        name="one_active_import_per_source",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="recipesource",
            name="import_source",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="recipe_source",
                to="recipes.importsource",
            ),
        ),
        migrations.CreateModel(
            name="RecipeImportAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("number", models.PositiveIntegerField()), ("lease_id", models.UUIDField()), ("started_at", models.DateTimeField(auto_now_add=True)), ("finished_at", models.DateTimeField(blank=True, null=True)), ("outcome", models.CharField(blank=True, max_length=12)), ("error_code", models.CharField(blank=True, max_length=80)),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attempts", to="recipes.recipeimportjob")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=["job", "number"], name="unique_import_attempt")]},
        ),
        migrations.AddConstraint(model_name="importsource", constraint=models.UniqueConstraint(fields=["household", "content_hash"], name="unique_import_source_hash")),
    ]