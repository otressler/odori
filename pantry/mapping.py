from dataclasses import dataclass

from django.conf import settings

from .models import CanonicalIngredient
from .semantic import (
    cosine_similarity,
    embed_with_diagnostics,
    fuzzy_similarity,
    normalized_text,
)


@dataclass(frozen=True)
class MappingCandidate:
    ingredient: CanonicalIngredient
    score: float
    text_score: float
    embedding_score: float | None
    method: str


@dataclass(frozen=True)
class MappingResult:
    candidate: MappingCandidate | None
    alternatives: tuple[MappingCandidate, ...]
    state: str
    provider_state: str
    model_version: str
    policy_version: str
    requires_confirmation: bool


def candidate_payload(candidate):
    return {
        "ingredient_id": str(candidate.ingredient.id),
        "name": candidate.ingredient.name,
        "score": candidate.score,
        "text_score": candidate.text_score,
        "embedding_score": candidate.embedding_score,
        "method": candidate.method,
    }


def _candidate(ingredient, source, query_vector):
    normalized_source = normalized_text(source)
    normalized_name = normalized_text(ingredient.name)
    normalized_aliases = [normalized_text(alias) for alias in ingredient.aliases]
    if normalized_source == normalized_name:
        return MappingCandidate(ingredient, 1.0, 1.0, None, "exact_name")
    if normalized_source in normalized_aliases:
        return MappingCandidate(ingredient, 1.0, 1.0, None, "exact_alias")

    text_score = max(
        [fuzzy_similarity(source, ingredient.name)]
        + [fuzzy_similarity(source, alias) for alias in ingredient.aliases]
    )
    embedding_score = None
    if (
        query_vector
        and ingredient.embedding
        and ingredient.embedding_model == settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    ):
        embedding_score = cosine_similarity(query_vector, ingredient.embedding)
    if embedding_score is None:
        score = text_score
        method = "fuzzy"
    else:
        score = (text_score * 0.65) + (embedding_score * 0.35)
        method = "combined"
    return MappingCandidate(ingredient, score, text_score, embedding_score, method)


def map_source_text(*, household, source_text, limit=3):
    source_text = (source_text or "").strip()
    policy_version = settings.INGREDIENT_MAPPING_POLICY_VERSION
    model_version = settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT
    if not source_text:
        return MappingResult(
            None, (), "unresolved", "skipped", model_version, policy_version, False
        )

    ingredients = CanonicalIngredient.objects.filter(household=household, active=True)
    embedding_result = embed_with_diagnostics(
        source_text,
        household_id=household.id,
        operation="ingredient_mapping",
    )
    ranked = sorted(
        (
            _candidate(ingredient, source_text, embedding_result.vector)
            for ingredient in ingredients
        ),
        key=lambda item: (-item.score, item.ingredient.name.casefold()),
    )
    ranked = [item for item in ranked if item.score >= settings.INGREDIENT_MAPPING_REVIEW_MIN_SCORE]
    top = ranked[0] if ranked else None
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    auto = bool(
        top
        and top.score >= settings.INGREDIENT_AUTO_MATCH_MIN_SCORE
        and top.score - runner_up >= settings.INGREDIENT_AUTO_MATCH_MIN_MARGIN
    )
    state = "matched" if auto else ("review_needed" if top else "unresolved")
    return MappingResult(
        top,
        tuple(ranked[1:limit]),
        state,
        embedding_result.state,
        model_version,
        policy_version,
        not auto,
    )


def assign_recipe_ingredient(*, user, recipe, line, ingredient):
    if recipe.household_id != ingredient.household_id:
        raise ValueError("Ingredient does not belong to this household.")
    if not ingredient.active:
        raise ValueError("Inactive ingredients cannot be assigned.")
    result = map_source_text(household=recipe.household, source_text=line.source_text)
    line.canonical_ingredient = ingredient
    line.match_state = line.MatchState.MATCHED
    line.match_method = "manual"
    line.match_score = result.candidate.score if result.candidate else None
    line.match_policy_version = result.policy_version
    line.match_embedding_model = result.model_version
    line.match_candidates = [
        candidate_payload(result.candidate),
        *(candidate_payload(candidate) for candidate in result.alternatives),
    ]
    line.save(
        update_fields=[
            "canonical_ingredient",
            "match_state",
            "match_method",
            "match_score",
            "match_policy_version",
            "match_embedding_model",
            "match_candidates",
        ]
    )
    return line
