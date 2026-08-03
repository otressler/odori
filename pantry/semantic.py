import json
import logging
import math
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache

from core.observability import bind_context, current_context, log_event, record_provider_diagnostic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list | None
    state: str
    error_code: str = ""
    http_status: int | None = None
    duration_ms: int = 0



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


def embed_with_diagnostics(text, *, household_id=None, job_id=None, operation="embedding"):
    """Return an embedding outcome without making provider failure a product-flow failure."""

    started = time.monotonic()
    endpoint = settings.AZURE_OPENAI_ENDPOINT
    deployment = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    api_key = settings.AZURE_OPENAI_API_KEY
    correlation_id = current_context().get("request_id")
    if not settings.INGREDIENT_EMBEDDINGS_ENABLED:
        result = EmbeddingResult(vector=None, state="skipped", error_code="disabled")
    elif not all((endpoint, deployment, api_key)):
        result = EmbeddingResult(vector=None, state="skipped", error_code="missing_configuration")
    else:
        result = _request_embedding(
            text,
            endpoint=endpoint,
            deployment=deployment,
            api_key=api_key,
        )
    duration_ms = round((time.monotonic() - started) * 1000)
    result = EmbeddingResult(
        vector=result.vector,
        state=result.state,
        error_code=result.error_code,
        http_status=result.http_status,
        duration_ms=duration_ms,
    )
    log_event(
        logger,
        "provider.embedding_completed",
        level=logging.WARNING if result.state != "succeeded" else logging.INFO,
        operation=operation,
        household_id=household_id,
        job_id=job_id,
        provider_state=result.state,
        error_code=result.error_code,
        http_status=result.http_status,
        deployment=deployment,
        vector_dimensions=len(result.vector) if result.vector else None,
        duration_ms=result.duration_ms,
    )
    if household_id:
        record_provider_diagnostic(
            household_id=household_id,
            correlation_id=correlation_id,
            job_id=job_id,
            operation=operation,
            state=result.state,
            error_code=result.error_code,
            http_status=result.http_status,
            deployment=deployment,
            vector_dimensions=len(result.vector) if result.vector else None,
            duration_ms=result.duration_ms,
        )
    return result


def _request_embedding(text, *, endpoint, deployment, api_key):
    request = Request(
        f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/embeddings?api-version=2024-10-21",
        data=json.dumps({"input": text}).encode(),
        headers={"Content-Type": "application/json", "api-key": api_key},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS) as response:
            vector = json.loads(response.read())["data"][0]["embedding"]
        if not isinstance(vector, list) or not vector:
            return EmbeddingResult(vector=None, state="failed", error_code="invalid_response")
        return EmbeddingResult(vector=vector, state="succeeded")
    except TimeoutError:
        return EmbeddingResult(vector=None, state="failed", error_code="timeout")
    except HTTPError as exc:
        return EmbeddingResult(
            vector=None,
            state="failed",
            error_code="http_error",
            http_status=exc.code,
        )
    except URLError:
        return EmbeddingResult(vector=None, state="failed", error_code="network_error")
    except (KeyError, ValueError, TypeError):
        return EmbeddingResult(vector=None, state="failed", error_code="invalid_response")


def embed(text):
    return embed_with_diagnostics(text).vector


def query_embedding(query):
    normalized_query = normalized_text(query)
    cache_key = "query-embedding:" + sha256(
        f"{settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT}:{normalized_query}".encode()
    ).hexdigest()
    vector = cache.get(cache_key)
    if vector is None:
        vector = embed(query)
        if vector is not None:
            cache.set(cache_key, vector, timeout=3600)
    return vector


def ingredient_embedding_text(ingredient):
    return " ".join([ingredient.name, *ingredient.aliases])


def embedding_needs_refresh(vector, model):
    return not vector or model != settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT


def update_embedding(ingredient, *, job_id=None, correlation_id=None):
    with_context = {"request_id": correlation_id} if correlation_id else {}
    with bind_context(**with_context):
        result = embed_with_diagnostics(
            ingredient_embedding_text(ingredient),
            household_id=ingredient.household_id,
            job_id=job_id,
            operation="ingredient_embedding",
        )
    vector = result.vector
    if vector is not None:
        ingredient.embedding = vector
        ingredient.embedding_model = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
        ingredient.save(update_fields=["embedding", "embedding_model"])
    return vector is not None


def rank_ingredients(ingredients, query):
    query_vector = query_embedding(query)
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