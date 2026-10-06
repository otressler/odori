from django.conf import settings

from pantry.semantic import (
    cosine_similarity,
    embed,
    fuzzy_similarity,
    normalized_text,
    query_embedding,
)


def recipe_search_text(recipe):
    ingredients = " ".join(line.source_text for line in recipe.ingredients.all())
    tags = " ".join(assignment.tag.name for assignment in recipe.tag_assignments.all())
    return " ".join(part for part in (recipe.title, recipe.description, ingredients, tags) if part)


def update_search_embedding(recipe):
    vector = embed(recipe_search_text(recipe))
    if vector is not None:
        recipe.search_embedding = vector
        recipe.search_embedding_model = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
        recipe.save(update_fields=["search_embedding", "search_embedding_model"])
    return vector is not None


# A literal hit is what the user typed, so it outranks any embedding neighbour.
STRONG_TEXT_SCORE = 0.8


def _text_score(query, recipe):
    """Weight literal hits by field: title, then tag, then ingredient, then description."""

    normalized_query = normalized_text(query)
    if not normalized_query:
        return 0.0
    title = normalized_text(recipe.title)
    if f" {title}".find(f" {normalized_query}") >= 0:
        return 1.0
    if normalized_query in title:
        return 0.95
    tags = [normalized_text(assignment.tag.name) for assignment in recipe.tag_assignments.all()]
    if normalized_query in tags:
        return 0.93
    if any(normalized_query in tag for tag in tags):
        return 0.9
    ingredients = normalized_text(" ".join(line.source_text for line in recipe.ingredients.all()))
    if normalized_query in ingredients:
        return 0.85
    searchable = normalized_text(recipe_search_text(recipe))
    if normalized_query in searchable:
        return 0.8
    words = normalized_query.split()
    if len(words) > 1 and all(word in searchable for word in words):
        return 0.8
    return max(
        (fuzzy_similarity(query, candidate) for candidate in searchable.split()),
        default=0.0,
    )


def score_recipes(recipes, query):
    """Return (recipe, score, method) for every match, best first."""

    query_vector = query_embedding(query)
    scored = []
    for recipe in recipes:
        vector_score = (
            cosine_similarity(query_vector, recipe.search_embedding) if query_vector else None
        )
        text_score = _text_score(query, recipe)
        if vector_score is None or text_score >= STRONG_TEXT_SCORE:
            score, method = text_score, "text"
            if vector_score is not None and vector_score > text_score:
                score = vector_score
        else:
            score, method = vector_score, "semantic"
        if score >= settings.INGREDIENT_SEARCH_MIN_SCORE:
            scored.append((recipe, score, method))
    return sorted(scored, key=lambda result: (-result[1], result[0].title.casefold()))


def rank_recipes(recipes, query):
    return [recipe for recipe, _, _ in score_recipes(recipes, query)]
