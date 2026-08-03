import math
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

MAX_INGREDIENTS = 100
MAX_STEPS = 100
MAX_TAGS = 20

_ROOT_KEYS = {"title", "description", "servings", "ingredients", "steps", "tags", "version"}
_INGREDIENT_KEYS = {
    "sourceText",
    "amount",
    "unit",
    "optional",
    "canonicalIngredientId",
}
_STEP_KEYS = {"body", "timerSeconds"}


@dataclass(frozen=True, slots=True)
class RecipeDraft:
    title: str | None = None
    description: str | None = None
    servings: int | None = None
    ingredients: tuple[dict, ...] | None = None
    steps: tuple[dict, ...] | None = None
    tags: tuple[str, ...] | None = None
    version: int | None = None

    def as_data(self):
        return {
            key: value
            for key, value in (
                ("title", self.title),
                ("description", self.description),
                ("servings", self.servings),
                ("ingredients", list(self.ingredients) if self.ingredients is not None else None),
                ("steps", list(self.steps) if self.steps is not None else None),
                ("tags", list(self.tags) if self.tags is not None else None),
                ("version", self.version),
            )
            if value is not None
        }


def _text(value, field, *, maximum, allow_blank=True):
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")
    value = value.strip()
    if not allow_blank and not value:
        raise ValueError(f"{field} cannot be blank.")
    if len(value) > maximum:
        raise ValueError(f"{field} is too long.")
    if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
        raise ValueError(f"{field} contains unsupported control characters.")
    return value


def _decimal(value, field):
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float | Decimal):
        raise ValueError(f"{field} must be numeric.")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{field} must be numeric.")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not result.is_finite() or result < 0 or result >= Decimal("10000000"):
        raise ValueError(f"{field} is outside the supported range.")
    if result.as_tuple().exponent < -2:
        raise ValueError(f"{field} can have at most two decimal places.")
    return result


def validate_recipe_draft(data, *, partial=False, generated=False):
    if not isinstance(data, dict):
        raise ValueError("Recipe data must be an object.")
    unknown = set(data) - _ROOT_KEYS
    if unknown:
        raise ValueError(f"Unknown recipe field: {sorted(unknown)[0]}.")
    values = {}
    if "title" in data:
        values["title"] = _text(data["title"], "title", maximum=200)
    if "description" in data:
        values["description"] = _text(data["description"], "description", maximum=5000)
    if "servings" in data:
        servings = data["servings"]
        if isinstance(servings, bool) or not isinstance(servings, int):
            raise ValueError("servings must be a positive whole number.")
        if not 1 <= servings <= 100:
            raise ValueError("servings must be between 1 and 100.")
        values["servings"] = servings
    if "version" in data:
        version = data["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError("version must be a positive whole number.")
        values["version"] = version

    if "ingredients" in data:
        raw = data["ingredients"]
        minimum = 1 if generated else 0
        if not isinstance(raw, list) or not minimum <= len(raw) <= MAX_INGREDIENTS:
            raise ValueError(
                f"ingredients must contain between {minimum} and {MAX_INGREDIENTS} items."
            )
        ingredients = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                raise ValueError(f"ingredients[{index}] must be an object.")
            unknown = set(item) - _INGREDIENT_KEYS
            if unknown:
                raise ValueError(f"Unknown ingredient field: {sorted(unknown)[0]}.")
            optional = item.get("optional", False)
            if not isinstance(optional, bool):
                raise ValueError(f"ingredients[{index}].optional must be true or false.")
            canonical_id = item.get("canonicalIngredientId")
            if generated and canonical_id is not None:
                raise ValueError("Generated recipes cannot provide canonical ingredient IDs.")
            if canonical_id is not None and not isinstance(canonical_id, str):
                raise ValueError(
                    f"ingredients[{index}].canonicalIngredientId must be text."
                )
            ingredients.append(
                {
                    "sourceText": _text(
                        item.get("sourceText"),
                        f"ingredients[{index}].sourceText",
                        maximum=300,
                        allow_blank=False,
                    ),
                    "amount": _decimal(item.get("amount"), f"ingredients[{index}].amount"),
                    "unit": _text(
                        item.get("unit", ""), f"ingredients[{index}].unit", maximum=40
                    ),
                    "optional": optional,
                    **(
                        {"canonicalIngredientId": canonical_id}
                        if canonical_id is not None
                        else {}
                    ),
                }
            )
        values["ingredients"] = tuple(ingredients)

    if "steps" in data:
        raw = data["steps"]
        minimum = 1 if generated else 0
        if not isinstance(raw, list) or not minimum <= len(raw) <= MAX_STEPS:
            raise ValueError(f"steps must contain between {minimum} and {MAX_STEPS} items.")
        steps = []
        for index, item in enumerate(raw):
            if isinstance(item, str) and not generated:
                item = {"body": item}
            if not isinstance(item, dict):
                raise ValueError(f"steps[{index}] must be an object.")
            unknown = set(item) - _STEP_KEYS
            if unknown:
                raise ValueError(f"Unknown step field: {sorted(unknown)[0]}.")
            timer = item.get("timerSeconds")
            if timer is not None and (
                isinstance(timer, bool) or not isinstance(timer, int) or not 1 <= timer <= 86400
            ):
                raise ValueError(f"steps[{index}].timerSeconds is outside the supported range.")
            steps.append(
                {
                    "body": _text(
                        item.get("body"),
                        f"steps[{index}].body",
                        maximum=5000,
                        allow_blank=False,
                    ),
                    "timerSeconds": timer,
                }
            )
        values["steps"] = tuple(steps)

    if "tags" in data:
        raw = data["tags"]
        if not isinstance(raw, list) or len(raw) > MAX_TAGS:
            raise ValueError(f"tags must be an array with at most {MAX_TAGS} items.")
        tags = tuple(
            dict.fromkeys(
                _text(tag, f"tags[{index}]", maximum=60, allow_blank=False).lower()
                for index, tag in enumerate(raw)
            )
        )
        values["tags"] = tags

    if not partial:
        required = {"title", "servings", "ingredients", "steps", "tags"}
        missing = required - set(data)
        if missing:
            raise ValueError(f"Missing recipe field: {sorted(missing)[0]}.")
    return RecipeDraft(**values)
