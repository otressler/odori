from django.core.management.base import BaseCommand

from pantry.models import CanonicalIngredient
from pantry.semantic import update_embedding


class Command(BaseCommand):
    help = "Creates missing Azure AI Foundry embeddings for active canonical ingredients."

    def handle(self, *args, **options):
        updated = 0
        for ingredient in CanonicalIngredient.objects.filter(active=True):
            if not ingredient.embedding and update_embedding(ingredient):
                updated += 1
        self.stdout.write(self.style.SUCCESS(f"Created {updated} ingredient embeddings."))