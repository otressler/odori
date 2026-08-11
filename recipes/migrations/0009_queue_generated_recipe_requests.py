from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("recipes", "0008_generatedreciperequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="generatedreciperequest",
            name="attempt_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="generatedreciperequest",
            name="correlation_id",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="generatedreciperequest",
            name="error_message",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="generatedreciperequest",
            name="idea",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="generatedreciperequest",
            name="started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="generatedreciperequest",
            name="finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="generatedreciperequest",
            name="state",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("succeeded", "Succeeded"),
                    ("failed", "Failed"),
                ],
                default="queued",
                max_length=12,
            ),
        ),
    ]
