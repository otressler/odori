import uuid

from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("pantry", "0005_ingredientcategory_embedding_and_more")]

    operations = [
        migrations.CreateModel(
            name="PantryCategorizationJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("state", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("succeeded", "Succeeded"), ("failed", "Failed")], default="queued", max_length=12)),
                ("assigned_count", models.PositiveIntegerField(default=0)),
                ("error_message", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("household", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pantry_categorization_jobs", to="core.household")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="pantrycategorizationjob",
            constraint=models.UniqueConstraint(condition=Q(state__in=["queued", "running"]), fields=("household",), name="one_active_pantry_categorization_job"),
        ),
    ]