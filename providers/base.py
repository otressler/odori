from typing import Protocol


class RecipeExtractionProvider(Protocol):
    """Boundary that keeps provider DTOs out of recipe domain entities."""

    def extract(self, source: bytes) -> str:
        """Return untrusted extracted recipe text."""
