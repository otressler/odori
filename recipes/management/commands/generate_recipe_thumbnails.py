from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from recipes.images import _recipe_thumbnail
from recipes.models import Recipe


class Command(BaseCommand):
    help = "Generate missing recipe thumbnails from already stored recipe images."

    def handle(self, *args, **options):
        processed = 0
        recipes = Recipe.objects.filter(image__gt="", thumbnail="").only("id", "image")
        for recipe in recipes.iterator():
            with recipe.image.open("rb") as image_file:
                thumbnail_bytes = _recipe_thumbnail(image_file.read())
            recipe.thumbnail.save(
                f"{recipe.id}.jpg",
                ContentFile(thumbnail_bytes),
                save=False,
            )
            recipe.save(update_fields=["thumbnail"])
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Processed {processed} recipe thumbnail(s)."))
