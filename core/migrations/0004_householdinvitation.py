import uuid

import core.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("core", "0003_add_operational_diagnostics")]

    operations = [
        migrations.CreateModel(
            name="HouseholdInvitation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("registration_code", models.CharField(default=core.models.registration_code, editable=False, max_length=6, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("active", models.BooleanField(default=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invitations", to="core.user")),
                ("household", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="invitations", to="core.household")),
            ],
        )
    ]