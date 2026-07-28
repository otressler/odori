import json
from pathlib import Path

from .models import IngredientCategory, IngredientCategoryExample
from .semantic import normalized_text

CATALOG_PATH = Path(__file__).with_name("data") / "category_examples.de.json"


def load_starter_catalog():
    with CATALOG_PATH.open(encoding="utf-8") as catalog_file:
        return json.load(catalog_file)


def sync_starter_catalog(*, household, dry_run=False):
    """Synchronize curated examples while preserving household-owned entries."""

    catalog = load_starter_catalog()
    result = {
        "categories_created": 0,
        "examples_created": 0,
        "examples_updated": 0,
        "deactivated": 0,
    }
    starter_keys_by_category = {}

    for category_data in catalog["categories"]:
        category, created = IngredientCategory.objects.get_or_create(
            household=household,
            name=category_data["name"],
            defaults={"sort_order": category_data["sort_order"]},
        )
        if created:
            result["categories_created"] += 1
        elif category.sort_order != category_data["sort_order"]:
            category.sort_order = category_data["sort_order"]
            if not dry_run:
                category.save(update_fields=["sort_order"])

        source_keys = set()
        for text in category_data["examples"]:
            source_key = f"{category_data['key']}:{normalized_text(text)}"
            source_keys.add(source_key)
            defaults = {
                "household": household,
                "text": text,
                "normalized_text": normalized_text(text),
                "source": IngredientCategoryExample.Source.STARTER,
                "active": True,
            }
            example = IngredientCategoryExample.objects.filter(
                category=category,
                source=IngredientCategoryExample.Source.STARTER,
                source_key=source_key,
            ).first()
            if example is None:
                result["examples_created"] += 1
                if not dry_run:
                    IngredientCategoryExample.objects.create(
                        category=category, source_key=source_key, **defaults
                    )
            elif any(
                getattr(example, field) != value
                for field, value in defaults.items()
                if field != "household"
            ):
                result["examples_updated"] += 1
                if not dry_run:
                    for field, value in defaults.items():
                        setattr(example, field, value)
                    example.embedding = []
                    example.embedding_model = ""
                    example.save()
        starter_keys_by_category[category.id] = source_keys

    for category_id, source_keys in starter_keys_by_category.items():
        stale_examples = IngredientCategoryExample.objects.filter(
            household=household,
            category_id=category_id,
            source=IngredientCategoryExample.Source.STARTER,
            active=True,
        ).exclude(source_key__in=source_keys)
        result["deactivated"] += stale_examples.count()
        if not dry_run:
            stale_examples.update(active=False)

    return result
