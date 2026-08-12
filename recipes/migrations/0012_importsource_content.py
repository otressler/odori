from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("recipes", "0011_merge_0007_thumbnail_0010_import")]

    operations = [
        migrations.AlterField(
            model_name="importsource",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("url", "URL"),
                    ("text", "Text"),
                    ("image", "Image"),
                    ("pdf", "PDF"),
                ],
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="importsource",
            name="content",
            field=models.TextField(blank=True),
        ),
    ]