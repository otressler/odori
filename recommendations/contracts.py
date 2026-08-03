from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class IngredientDetail:
    canonical_id: str | None
    name: str
    unresolved: bool = False


@dataclass(frozen=True, slots=True)
class CandidateFeatures:
    recipe_id: str
    recipe_version: int
    title: str
    matched: int
    missing: int
    unknown: int
    days_since_cook: int
    preferred_tag_matches: int
    planned_occurrences: int
    feedback_state: str | None
    matched_ingredients: tuple[IngredientDetail, ...] = ()
    missing_ingredients: tuple[IngredientDetail, ...] = ()
    unknown_ingredients: tuple[IngredientDetail, ...] = ()
    unresolved_count: int = 0

    @property
    def total(self) -> int:
        return self.matched + self.missing + self.unknown


@dataclass(frozen=True, slots=True)
class ScoreComponents:
    inventory_coverage_bp: int
    missing_penalty_bp: int
    unknown_penalty_bp: int
    cook_recency_bp: int
    preferred_tags_bp: int
    already_planned_penalty_bp: int
    negative_feedback_penalty_bp: int

    def as_dict(self) -> dict[str, int]:
        return {
            "inventoryCoverageBp": self.inventory_coverage_bp,
            "missingPenaltyBp": self.missing_penalty_bp,
            "unknownPenaltyBp": self.unknown_penalty_bp,
            "cookRecencyBp": self.cook_recency_bp,
            "preferredTagsBp": self.preferred_tags_bp,
            "alreadyPlannedPenaltyBp": self.already_planned_penalty_bp,
            "negativeFeedbackPenaltyBp": self.negative_feedback_penalty_bp,
        }


@dataclass(frozen=True, slots=True)
class RecommendationReason:
    code: str


@dataclass(frozen=True, slots=True)
class RecommendationSuggestion:
    candidate: CandidateFeatures
    score_bp: int
    components: ScoreComponents
    reasons: tuple[RecommendationReason, ...]


@dataclass(frozen=True, slots=True)
class RecommendationOptions:
    week_start: date
    preferred_tag_ids: tuple[str, ...] = ()
    limit: int = 20
    scoring_version: str = "catalog-v1"


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    run_id: str | None
    scoring_version: str
    as_of: datetime
    inventory_snapshot_at: datetime
    week_start: date
    candidate_set_truncated: bool
    candidate_count: int
    query_duration_ms: int
    scoring_duration_ms: int
    suggestions: tuple[RecommendationSuggestion, ...]
