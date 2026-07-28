from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("pantry", "0009_category_examples")]

    operations = [
        migrations.AlterField(
            model_name="ingredientcategoryexample",
            name="source",
            field=models.CharField(
                choices=[
                    ("starter", "Starter catalog"),
                    ("owner", "Owner"),
                    ("assigned", "Automatic assignment"),
                    ("confirmed", "Confirmed assignment"),
                    ("imported", "Imported"),
                ],
                max_length=12,
            ),
        ),
    ]