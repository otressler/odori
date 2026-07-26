import json
import math
import re
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


def normalized_text(value):
    value = value.casefold().replace("ß", "ss")
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()
    return value


def _stem(value):
    value = normalized_text(value)
    if value.endswith("en") and len(value) > 4:
        return value[:-2]
    if value.endswith("e") and len(value) > 3:
        return value[:-1]
    return value


def _cosine(left, right):
    if not left or len(left) != len(right):
        return None
    magnitude = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if not magnitude:
        return None
    return sum(first * second for first, second in zip(left, right)) / magnitude


def cosine_similarity(left, right):
    return _cosine(left, right)


def _fuzzy_score(query, candidate):
    query = _stem(query)
    candidate = _stem(candidate)
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if query.endswith(candidate) or candidate.endswith(query):
        return 0.92
    return SequenceMatcher(None, query, candidate).ratio()


def fuzzy_similarity(query, candidate):
    return _fuzzy_score(query, candidate)


def embed(text):
    """Return a Foundry embedding when configured, otherwise leave matching offline."""

    endpoint = settings.AZURE_OPENAI_ENDPOINT
    deployment = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    api_key = settings.AZURE_OPENAI_API_KEY
    if not settings.INGREDIENT_EMBEDDINGS_ENABLED or not all((endpoint, deployment, api_key)):
        return None
    request = Request(
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/embeddings?api-version=2024-10-21",
        data=json.dumps({"input": text}).encode(),
        headers={"Content-Type": "application/json", "api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS) as response:
            return json.loads(response.read())["data"][0]["embedding"]
    except (HTTPError, URLError, KeyError, ValueError, TimeoutError):
        return None


def ingredient_embedding_text(ingredient):
    return " ".join([ingredient.name, *ingredient.aliases])


def update_embedding(ingredient):
    vector = embed(ingredient_embedding_text(ingredient))
    if vector is not None:
        ingredient.embedding = vector
        ingredient.embedding_model = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
        ingredient.save(update_fields=["embedding", "embedding_model"])
    return vector is not None


def rank_ingredients(ingredients, query):
    query_vector = embed(query)
    ranked = []
    for ingredient in ingredients:
        vector_score = _cosine(query_vector, ingredient.embedding) if query_vector else None
        text_score = max(
            [_fuzzy_score(query, ingredient.name)]
            + [_fuzzy_score(query, alias) for alias in ingredient.aliases]
        )
        score = vector_score if vector_score is not None else text_score
        if score >= settings.INGREDIENT_SEARCH_MIN_SCORE:
            ranked.append((ingredient, score, vector_score is not None))
    return sorted(ranked, key=lambda result: (-result[1], result[0].name.casefold()))


def best_match(ingredients, source_text):
    ranked = rank_ingredients(ingredients, source_text)
    if not ranked:
        return None
    ingredient, score, _ = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if score >= settings.INGREDIENT_AUTO_MATCH_MIN_SCORE and score - runner_up >= 0.08:
        return ingredient
    return None