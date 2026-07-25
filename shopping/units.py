"""Unit handling for shopping calculation.

The initial release deliberately performs no unit conversion. Normalization only
maps spelling variants onto one canonical key so that identical units can be
summed; `g` and `kg` stay separate components.
"""

UNIT_ALIASES = {
    "g": "g",
    "gr": "g",
    "gramm": "g",
    "gramme": "g",
    "kg": "kg",
    "kilo": "kg",
    "kilogramm": "kg",
    "mg": "mg",
    "ml": "ml",
    "milliliter": "ml",
    "cl": "cl",
    "l": "l",
    "liter": "l",
    "el": "el",
    "esslöffel": "el",
    "essloeffel": "el",
    "tl": "tl",
    "teelöffel": "tl",
    "teeloeffel": "tl",
    "stk": "stk",
    "stück": "stk",
    "stueck": "stk",
    "st": "stk",
    "prise": "prise",
    "prisen": "prise",
    "bund": "bund",
    "zehe": "zehe",
    "zehen": "zehe",
    "dose": "dose",
    "dosen": "dose",
    "packung": "packung",
    "packungen": "packung",
    "pkg": "packung",
    "tasse": "tasse",
    "tassen": "tasse",
    "blatt": "blatt",
    "scheibe": "scheibe",
    "scheiben": "scheibe",
}

UNIT_DISPLAY = {
    "g": "g",
    "kg": "kg",
    "mg": "mg",
    "ml": "ml",
    "cl": "cl",
    "l": "l",
    "el": "EL",
    "tl": "TL",
    "stk": "Stk.",
    "prise": "Prise",
    "bund": "Bund",
    "zehe": "Zehen",
    "dose": "Dose",
    "packung": "Packung",
    "tasse": "Tasse",
    "blatt": "Blatt",
    "scheibe": "Scheiben",
}


def normalize_unit(unit):
    cleaned = (unit or "").strip().lower().rstrip(".")
    if not cleaned:
        return ""
    return UNIT_ALIASES.get(cleaned, cleaned)


def display_unit(normalized):
    if not normalized:
        return ""
    return UNIT_DISPLAY.get(normalized, normalized)
