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


def _text_score(query, recipe):
    normalized_query = normalized_text(query)
    searchable = normalized_text(recipe_search_text(recipe))
    if normalized_query in searchable:
        return 1.0
    return max(
        (fuzzy_similarity(query, candidate) for candidate in searchable.split()),
        default=0.0,
    )


def rank_recipes(recipes, query):
    query_vector = query_embedding(query)
    ranked = []
    for recipe in recipes:
        vector_score = (
            cosine_similarity(query_vector, recipe.search_embedding) if query_vector else None
        )
        text_score = _text_score(query, recipe)
        score = vector_score if vector_score is not None else text_score
        if score >= settings.INGREDIENT_SEARCH_MIN_SCORE:
            ranked.append((recipe, score))
    return [
        recipe
        for recipe, _ in sorted(ranked, key=lambda result: (-result[1], result[0].title.casefold()))
    ]
