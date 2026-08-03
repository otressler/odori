from dataclasses import dataclass
from typing import Protocol

from recipes.contracts import RecipeDraft


@dataclass(frozen=True, slots=True)
class RecipeGenerationRequest:
    context_json: str
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class RecipeGenerationResult:
    draft: RecipeDraft
    input_tokens: int
    output_tokens: int


class RecipeGenerationProvider(Protocol):
    def generate(self, request: RecipeGenerationRequest, **diagnostic_context):
        """Return a validated draft without retaining provider content."""


class RecipeGenerationError(Exception):
    def __init__(
        self,
        *,
        error_code,
        retryable,
        http_status=None,
        input_tokens=0,
        output_tokens=0,
    ):
        super().__init__("Recipe generation could not be completed.")
        self.error_code = error_code
        self.retryable = retryable
        self.http_status = http_status
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
