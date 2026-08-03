from collections.abc import Callable

from .contracts import (
    CandidateFeatures,
    RecommendationReason,
    RecommendationSuggestion,
    ScoreComponents,
)

CATALOG_V1 = "catalog-v1"
REASON_ORDER = (
    "inventory_coverage",
    "missing_ingredients",
    "unknown_ingredients",
    "cook_recency",
    "preferred_tags",
    "already_planned",
    "negative_feedback",
)


def score_catalog_v1(
    candidate: CandidateFeatures, *, selected_tag_count: int
) -> RecommendationSuggestion:
    total = candidate.total
    coverage = (6500 * candidate.matched) // total if total else 0
    missing = (2000 * candidate.missing) // total if total else 0
    unknown = (1000 * candidate.unknown) // total if total else 0
    recency = (1500 * candidate.days_since_cook) // 21
    preferred = (
        (1000 * candidate.preferred_tag_matches) // selected_tag_count
        if selected_tag_count
        else 0
    )
    planned = 1500 if candidate.planned_occurrences > 0 else 0
    negative = 2500 if candidate.feedback_state else 0
    components = ScoreComponents(
        inventory_coverage_bp=coverage,
        missing_penalty_bp=missing,
        unknown_penalty_bp=unknown,
        cook_recency_bp=recency,
        preferred_tags_bp=preferred,
        already_planned_penalty_bp=planned,
        negative_feedback_penalty_bp=negative,
    )
    raw_score = coverage - missing - unknown + recency + preferred - planned - negative
    score = min(10000, max(0, raw_score))
    applicable = {
        "inventory_coverage": candidate.matched > 0,
        "missing_ingredients": candidate.missing > 0,
        "unknown_ingredients": candidate.unknown > 0,
        "cook_recency": candidate.days_since_cook > 0,
        "preferred_tags": candidate.preferred_tag_matches > 0,
        "already_planned": candidate.planned_occurrences > 0,
        "negative_feedback": bool(candidate.feedback_state),
    }
    reasons = tuple(
        RecommendationReason(code=code) for code in REASON_ORDER if applicable[code]
    )
    return RecommendationSuggestion(
        candidate=candidate,
        score_bp=score,
        components=components,
        reasons=reasons,
    )


Scorer = Callable[..., RecommendationSuggestion]
SCORERS: dict[str, Scorer] = {CATALOG_V1: score_catalog_v1}


def score_candidate(
    candidate: CandidateFeatures, *, scoring_version: str, selected_tag_count: int
) -> RecommendationSuggestion:
    try:
        scorer = SCORERS[scoring_version]
    except KeyError as exc:
        raise ValueError(f"Unsupported scoring version: {scoring_version}") from exc
    return scorer(candidate, selected_tag_count=selected_tag_count)
