from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("pantry", "0006_pantrycategorizationjob"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingredientcategory",
            name="description",
            field=models.TextField(blank=True),
        ),
    ]
