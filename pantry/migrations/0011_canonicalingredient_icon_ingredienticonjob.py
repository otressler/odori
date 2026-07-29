from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [("pantry", "0010_alter_category_example_source")]

    operations = [
        migrations.AddField(
            model_name="canonicalingredient",
            name="icon",
            field=models.FileField(blank=True, upload_to="pantry-icons/"),
        ),
        migrations.AddField(
            model_name="canonicalingredient",
            name="icon_prompt",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="canonicalingredient",
            name="icon_status",
            field=models.CharField(
                choices=[("pending", "Ausstehend"), ("ready", "Fertig"), ("failed", "Fehlgeschlagen")],
                default="pending",
                max_length=12,
            ),
        ),
        migrations.CreateModel(
            name="IngredientIconJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("prompt", models.TextField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                            ("superseded", "Superseded"),
                        ],
                        default="queued",
                        max_length=12,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                ("error_code", models.CharField(blank=True, max_length=80)),
                ("correlation_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "ingredient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="icon_jobs",
                        to="pantry.canonicalingredient",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]