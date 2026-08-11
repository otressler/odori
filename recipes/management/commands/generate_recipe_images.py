from django.core.management.base import BaseCommand

from recipes.images import run_next_recipe_image_job


class Command(BaseCommand):
    help = "Generate pending recipe images using the configured Azure OpenAI image deployment."

    def handle(self, *args, **options):
        processed = 0
        while run_next_recipe_image_job():
            processed += 1
        self.stdout.write(self.style.SUCCESS(f"Processed {processed} recipe image job(s)."))
