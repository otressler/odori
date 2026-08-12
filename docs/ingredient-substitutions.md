# Ingredient substitutions: implementation handoff

## Purpose

Allow a household to deliberately use a different ingredient for one planned
meal without changing the recipe. For example, a recipe requiring spaghetti
can use tagliatelle for Tuesday's dinner. The effective ingredient list for
that meal and its calculated shopping list must reflect the accepted choice.

This is a planning decision, not a recipe edit, pantry inference, dietary
recommendation, or automatic replacement feature.

## Non-negotiable rules

1. **Every application needs explicit user acceptance.** A rule, prior choice,
   high confidence, or pantry availability may surface a suggestion but must
   never apply it automatically.
2. **Recipes remain immutable.** Do not modify `RecipeIngredient`,
   `source_text`, canonical ingredient assignment, amounts, units, steps, or
   recipe versions when a substitution is accepted.
3. **A decision belongs to one meal slot and one recipe ingredient line.**
   It may affect the calculated shopping list only through that slot.
4. **Rules are directional.** A spaghetti → tagliatelle rule does not imply
   tagliatelle → spaghetti. Do not follow multiple rules transitively.
5. **No safety claim.** UI must say that household substitutions are personal
   cooking notes, not allergen, dietary, or nutritional advice.
6. **No silent learning.** Only successful explicit acceptance contributes to
   future “used before” suggestions. Dismissal, cancellation, display, and
   shopping-item viewing are not evidence.
7. **Historical decisions stay intact.** Editing, deactivating, or deleting a
   rule must not rewrite already accepted slot decisions or historical lists.

## Scope and user journeys

### A. Author a household rule

An authorized household member creates a rule:

- original canonical ingredient: `spaghetti`
- substitute canonical ingredient: `tagliatelle`
- optional note: “similar cooking time; use the same amount”
- optional suitability scope: initially a short free-text note only; do not
  implement ratios, medical claims, or global seed data in the first version

Rules should be editable and deactivatable. They are candidate suggestions,
not standing approvals.

### B. Review a planned meal before cooking

Each recipe meal card in the week view gets an **“Ingredients”** or
**“Prepare ingredients”** action that opens a dedicated pre-cooking page. It
is separate from Kitchen Mode and works before cooking starts.

The page shows:

- recipe title, planned date/meal slot, and planned servings
- every recipe ingredient with scaled amount/unit and pantry availability
- accepted effective choice where present:
  `Tagliatelle instead of spaghetti`
- eligible directional household rules for the original ingredient
- rule notes and availability of each candidate substitute
- actions to select a candidate, explicitly confirm it, or revert an accepted
  decision
- a clear notice that the recipe itself is unchanged
- an omission action (defined below) and an explicit confirmation

An accepted choice should be visible on the week card without making the card
busy: e.g. a count badge, “1 substitution”, linking to the pre-cooking page.
The page should still be usable when JavaScript is unavailable.

### C. Kitchen Mode

Kitchen Mode consumes the same effective planned ingredient list. It must show
the original wording plus the planned change, rather than hiding the recipe
line. Example:

`500 g spaghetti — use 500 g tagliatelle instead`

Kitchen Mode may provide the same substitute/revert actions if the meal is not
yet cooked, but it must not become the only management surface. The
pre-cooking page is the primary place to review choices before shopping or
cooking.

### D. Shopping view

Calculated shopping items must make substitution possibilities visible:

- For an open calculated item with eligible rules, show a concise “Alternative
  available” affordance.
- Explain which substitute is available and which upcoming meal slots require
  the original.
- Selecting an alternative must lead to explicit confirmation for the affected
  slot(s), not replace a whole aggregated shopping item without context.
- Once accepted, regenerate or refresh calculated entries so the original
  contribution from that slot is removed and the substitute contribution is
  added.
- Show provenance on the affected item: recipe title, planned date, slot ID,
  original ingredient, and accepted substitute/omission.

Do not offer a substitute action for manual shopping items in the first
version; manual entries are not tied to a recipe line or slot. Do show
available rule information only where the result can be safely tied to an
upcoming planned meal.

### E. Post-add suggestion

After a meal is added to the weekly plan, query high-confidence learned
choices for its ingredient lines. If any qualify, present a **non-blocking**
review prompt. It must say, for example:

> For this meal, your household previously used tagliatelle instead of
> spaghetti.

Per proposed choice, provide:

- **Review and accept**: opens the pre-cooking page or an explicit confirmation
  UI; it does not accept on click alone.
- **Keep original**: closes the suggestion and changes nothing.
- **Dismiss**: closes the suggestion and changes nothing.

The prompt must not block adding the meal, must be keyboard accessible, and
must never alter the shopping list until a decision is accepted.

## Data model

Keep this data in `planning` because applications are per meal slot. Household
rules can live in `pantry` because they connect canonical ingredients, but
placing all substitution models in `planning` is acceptable if ownership and
imports remain simple. Whichever app owns the models, keep cross-app foreign
key dependencies explicit in migrations.

### `IngredientSubstitutionRule`

Household-owned, directional candidate rule:

| Field | Notes |
| --- | --- |
| `id` | UUID primary key |
| `household` | Required ownership boundary |
| `original_ingredient` | FK to `CanonicalIngredient`; the required ingredient |
| `substitute_ingredient` | Nullable FK to `CanonicalIngredient`; null is not used for omission—see below |
| `note` | Optional short household cooking note |
| `active` | Deactivate without erasing history |
| `created_by`, `created_at`, `updated_at` | Audit and display |

Constraints and validation:

- Both ingredients must belong to the rule household.
- Original and substitute must differ.
- Make the pair unique per household, or define an intentional uniqueness
  policy before implementation. A unique active directional pair is simplest.
- Do not prohibit inverse rules; they are independently authored and remain
  directional.
- No recursive resolution or automatic cycle processing is needed.

### `MealSlotIngredientDecision`

The immutable-enough record of an accepted planned-session choice:

| Field | Notes |
| --- | --- |
| `id` | UUID primary key |
| `meal_slot` | FK to the target `MealSlot` |
| `recipe_ingredient` | FK to the original `RecipeIngredient` |
| `decision_type` | `substitute` or `omit` |
| `substitute_ingredient` | Required for `substitute`, null for `omit` |
| `rule` | Nullable FK to the source rule; preserve even after rule deactivation |
| `accepted_by`, `accepted_at` | Explicit user acceptance audit |
| `reverted_by`, `reverted_at` | Prefer a reversible state/audit over deletion if product history needs it |
| `version` | Optimistic concurrency for the decision; slot version must also be checked |

Enforce one active decision per `(meal_slot, recipe_ingredient)`. A replacement
of tagliatelle with another accepted option supersedes or reverts the previous
decision in the same transaction; it must never yield two active effective
ingredients.

Validate in the service layer, under transaction locks:

- slot belongs to the acting user’s household
- slot is a recipe entry, has a recipe, and is not cooked
- recipe ingredient belongs to the slot’s recipe
- substitute and rule, where present, belong to the household
- if supplied, the rule is active and exactly matches original → substitute
- omission has no substitute ingredient and no invented canonical ingredient

The decision must capture the original line by ID rather than only canonical
ingredient ID: a recipe can contain repeated ingredients with distinct units,
amounts, or preparation text.

### Learning aggregate

Do not store an opaque confidence value that is updated without traceability.
Derive the initial learned ranking from active accepted decisions:

- group by household, original ingredient, substitute ingredient, and
  `decision_type`
- count accepted uses
- optionally prefer recent uses with a documented deterministic time decay
- exclude reverted choices from active “used before” suggestions

For first delivery, use a simple threshold such as at least three accepted
uses and at least 80% of the household’s non-omitted decisions for the same
original ingredient. The exact values must be product-configurable constants,
not hard-coded throughout templates. “High confidence” means recurrence of
explicit household choices, never nutritional equivalence.

Do not learn a rule automatically from a one-off accepted substitute unless
the product explicitly introduces that rule-creation workflow. It is safer to
let learning make a suggestion while the user still approves each application.

## Effective ingredient resolution

Create one service-level resolver used by the pre-cooking page, Kitchen Mode,
shopping aggregation, APIs, and future recommendations. Do not duplicate this
logic in views/templates.

For each `RecipeIngredient` in a non-cooked planned recipe slot:

1. Start with original line, canonical ingredient, scaled amount, unit, and
   source text.
2. Load its single active accepted decision, if present.
3. If `substitute`, set the effective canonical ingredient to the accepted
   substitute while retaining original line/provenance.
4. If `omit`, mark it omitted and produce no effective shopping contribution.
5. If no decision, use the original canonical ingredient and line.

The resolver returns both original and effective values. It must not mutate
models while reading. Unmapped recipe lines remain unmodified unless a future
feature defines a safe mapping workflow.

## Shopping-list integration

This is a required cross-feature dependency, not a UI-only enhancement.
`shopping.services.collect_aggregates` currently iterates recipe ingredient
lines directly. Change it to consume the effective planned ingredient resolver.

### Aggregation rules

- A normal line contributes the original canonical ingredient and scaled
  amount/unit.
- A substitute decision contributes the substitute canonical ingredient under
  the same scaled amount/unit unless future ratio support is explicitly added.
- An omit decision contributes nothing.
- An ingredient with no canonical mapping keeps the existing text-based
  aggregation behavior unless omitted.
- Existing unit compatibility behavior remains unchanged: only compatible
  normalized units sum; incompatible or unspecified quantities stay as
  separate components.
- Inventory exclusion is evaluated against the **effective** ingredient. If
  tagliatelle is in stock, the accepted spaghetti → tagliatelle contribution
  is excluded. Spaghetti is not added for that slot.

### Provenance

Extend calculated item references or introduce a component-level provenance
structure so the list can explain why a line exists. Each reference must
include at least:

- meal slot ID, date, recipe ID/title
- original recipe ingredient ID and original ingredient/label
- effective ingredient/label
- decision ID and type when applicable

Avoid treating the existing recipe-level `recipe_refs` as sufficient: it
cannot explain multiple different ingredient decisions within one recipe.
Any JSON schema change requires migration/backwards-compatibility handling for
existing active lists.

### Regeneration semantics

Existing behavior preserves purchased, skipped, and manual items on
regeneration. Maintain it:

- A new accepted/reverted decision affects only calculated, still-open items.
- Do not silently alter a purchased or skipped item; retain it as a historical
  shopping decision.
- Tell the user when the list has been recalculated or is now out of date.
- Prefer explicit **“Update shopping list”** after a pre-cooking decision for
  the first version, or consistently regenerate synchronously as part of the
  acceptance transaction. Choose one policy and document it in UI/API tests.
- If synchronous regeneration is used, it must be inside a transaction with
  decision updates and use list/slot versions to avoid overwriting concurrent
  shopping changes.

## Omission as a special decision

Treat “omit” as a dedicated `MealSlotIngredientDecision.decision_type`, not as
a fake `CanonicalIngredient` and not as a normal global substitution rule.

Why:

- It is recipe-line and meal-specific rather than an ingredient identity.
- It should not appear as a pantry item, shopping item, or substitute target.
- It should not imply that every future recipe may omit the ingredient.
- It has different UI and safety wording.

The user must explicitly confirm: “Omit [original line] from this planned
meal. This changes the planned ingredient and shopping calculation, not the
recipe.” Display omitted lines in pre-cooking and Kitchen Mode. Optional
recipe lines can offer a shorter omission control, but still require an
intentional action and record.

Omission may contribute to learned *omission* suggestions only after the same
explicit-confidence thresholds. Phrase such a prompt carefully (“You have
previously omitted…”) and never make it a default.

## UI details and accessibility

- Use server-rendered forms with POST endpoints, CSRF protection, and visible
  success/error messages; JavaScript may enhance confirmation dialogs.
- Confirmation controls must identify original ingredient, substitute/omit
  choice, meal title/date, and shopping consequence.
- Revert controls require confirmation when an active shopping list has
  calculated entries affected by the decision.
- Do not rely on color to distinguish substituted, omitted, or original
  ingredients. Include readable text and status labels.
- On the week card, provide an accessible label such as “Review ingredients
  for Sugo; one substitution selected.”
- Do not expose household rule notes or plan references across household
  boundaries.

## Service and API work

Add service operations rather than embedding writes in page views:

- list eligible rules for one slot ingredient
- resolve effective ingredient lines for a slot
- accept substitute decision
- accept omission decision
- revert/supersede a decision
- calculate learned candidates and high-confidence candidates

All mutation operations need `transaction.atomic`, household filtering, row
locks as appropriate, and optimistic concurrency. Check the meal slot version
provided by the caller; increment it when effective planned ingredients
change, because its shopping/cooking representation changed.

Suggested endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/meal-slots/{slotId}/ingredients` | Original/effective lines, decisions, candidate rules, pantry status |
| `POST` | `/api/v1/meal-slots/{slotId}/ingredient-decisions` | Explicit substitute/omit acceptance with slot version |
| `DELETE` | `/api/v1/meal-slots/{slotId}/ingredient-decisions/{id}` | Revert an active decision with slot version |
| `GET` | `/api/v1/shopping-items/{itemId}/substitution-options` | Affected open planned slots and available choices |
| `GET/POST/PATCH` | household substitution-rule endpoint | Manage directional rules |

Expose accepted effective-line data in the meal-plan API so non-HTML clients
do not recompute it. Return `409 stale_version` with the current slot version
for conflicts, matching existing plan behavior.

## Recommended implementation order

1. Add models, migrations, ownership validation, and rule-management service.
2. Implement effective-line resolver and thorough unit tests.
3. Add decision acceptance/reversion services, slot version increments, and
   API tests.
4. Update shopping aggregation and calculated-item provenance; test
   regeneration, stock exclusion, and preservation of purchased/skipped/manual
   rows.
5. Build the pre-cooking page and week-card entry point.
6. Render effective lines in Kitchen Mode.
7. Add shopping-list substitute visibility and confirmation routing.
8. Add learned rankings and high-confidence post-add prompt.
9. Update API specification, domain model, product requirements, and backlog
   status once behavior is delivered.

## Cross-feature dependencies and risks

| Dependency | Required behavior |
| --- | --- |
| Canonical ingredient normalization | Rules require stable, household-owned canonical IDs and aliases; do not launch broad rule authoring before normalization quality is acceptable. |
| Meal planning | Decisions attach to `MealSlot` + `RecipeIngredient`, use planned servings, respect cooked state, and increment slot versions. |
| Shopping | Must aggregate effective, not original, ingredients; refresh policy and calculated-item provenance are part of the feature. |
| Pantry | Availability display and shopping exclusion use effective substitute IDs. Do not mark an original ingredient unavailable merely because it was replaced. |
| Kitchen Mode | Must render original/effective provenance and share the resolver with pre-cooking. |
| Inventory/cook events | Cooking does not infer depletion. Any selected depletion action must validate against effective ingredients as well as original recipe lines, so a substituted ingredient can be marked for replenishment safely. |
| Recommendations | Initial delivery may only display rule availability. If scoring treats substitutes as coverage later, it needs an explicit, explainable weighting and must still not auto-apply a decision. |
| Collaboration/concurrency | Slot and shopping updates can race across household members. Reject stale decisions; never overwrite calculated-list states silently. |
| Data retention/history | Keep accepted/reverted decision audit records and list provenance long enough to explain historical lists; rule deactivation must not erase history. |

## Acceptance test matrix

At minimum, add service, API, and page tests for:

1. Spaghetti → tagliatelle is offered only when an active directional household
   rule exists; the reverse is not inferred.
2. A user must explicitly accept before the effective line or shopping list
   changes.
3. Acceptance changes only one planned slot and one recipe line; recipe data
   remains byte-for-byte unchanged.
4. Pre-cooking page and Kitchen Mode show the same effective result.
5. Reverting restores the original effective line and, after the selected
   refresh policy, original shopping contribution.
6. Substitute contributions aggregate under tagliatelle, use scaled amounts,
   respect unit rules, and exclude tagliatelle when it is `in_stock`.
7. Omission produces no shopping contribution, is visibly labelled, and never
   creates a pantry ingredient.
8. Existing purchased, skipped, and manual shopping rows survive regeneration.
9. Calculated shopping provenance identifies the affected meal/line/decision.
10. Cooked, note, leftovers, foreign-household, mismatched-line, mismatched
    rule, and stale-version requests are rejected.
11. A substituted effective ingredient can be selected for explicit
    cook-triggered inventory replenishment; unrelated ingredients cannot.
12. Learning counts accepted household decisions only; it does not react to
    dismissals or unaccepted candidates.
13. High-confidence prompts are non-blocking and do not auto-apply or
    regenerate shopping data.
14. Rule deactivation prevents new suggestions but preserves displayed history
    for existing accepted decisions.

## Deferred decisions

Do not include these in the initial implementation without a separate product
decision:

- quantity/ratio conversions for substitutes
- automatic creation of rules from decisions
- global, AI-generated, dietary, medical, or allergen substitution data
- transitive substitutions and cycle ranking
- applying substitutions directly to manual shopping items
- treating substitutes as full recommendation coverage
- multi-slot bulk acceptance without reviewing each affected meal

