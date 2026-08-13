from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0005_delete_auditcontext")]

    operations = [
        migrations.AlterField(
            model_name="householdmembership",
            name="role",
            field=models.CharField(
                choices=[
                    ("owner", "Owner"),
                    ("admin", "Admin"),
                    ("member", "Member"),
                ],
                max_length=12,
            ),
        ),
    ]
