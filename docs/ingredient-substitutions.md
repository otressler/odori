# Ingredient substitutions: implementation handoff

> **Before implementation:** Verify this handoff against the current models,
> services, APIs, templates, migrations, and tests. Update the plan and its
> linked contracts for any implementation drift; do not assume the structures
> named here still exist or have the same responsibilities.

## Purpose

Allow a household to deliberately use a different ingredient for one planned
meal without changing the recipe. For example, a recipe requiring spaghetti
can use tagliatelle for Tuesday's dinner. The effective ingredient list for
that meal and its calculated shopping list must reflect the accepted choice.

This is an optional planning decision, not a recipe edit, pantry inference,
dietary recommendation, or automatic replacement feature. Pantry availability
and restocking are separate concepts: an inventory item is `available`,
`unknown`, or `unavailable`; an item being on the shopping list indicates
restocking intent and does not change its pantry state.

## Non-negotiable rules

1. **Every application needs explicit user acceptance.** Prior choices,
   similarity, or pantry availability may surface a suggestion but must never
   apply it automatically.
2. **Recipes remain immutable.** Do not modify `RecipeIngredient`,
   `source_text`, canonical ingredient assignment, amounts, units, steps, or
   recipe versions when a substitution is accepted.
3. **A decision belongs to one meal slot and one recipe ingredient line.**
   It may affect the calculated shopping list only through that slot.
4. **Pantry state is not restocking state.** Only `available` means the
   household currently has an ingredient. `unknown` means availability has not
   been confirmed, and `unavailable` means the household has explicitly marked
   it missing. Being on the shopping list means restocking is planned; it does
   not make an ingredient available.
5. **No safety claim.** UI must say that household substitutions are personal
   cooking notes, not allergen, dietary, or nutritional advice.
6. **No silent learning.** Only successful explicit acceptance contributes to
   future “used before” suggestions. Dismissal, cancellation, display, and
   shopping-item viewing are not evidence.
7. **Recipes remain the only permanent-edit surface.** Reusable household
   substitution rules, post-add prompts, and shopping-side substitution
   controls are deferred until the planning flow proves useful.

## Scope and user journeys

### A. Review a planned meal before cooking

Each recipe meal card in the week view gets an **“Ingredients”** or
**“Prepare ingredients”** action that opens a dedicated pre-cooking page. It
is separate from Kitchen Mode and works before cooking starts.

The page shows:

- recipe title, planned date/meal slot, and planned servings
- every recipe ingredient with scaled amount/unit, pantry state, and whether it
  is on the shopping list
- accepted effective choice where present:
  `Tagliatelle instead of spaghetti`
- similar canonical ingredients and previously accepted substitutions
- pantry state and restocking status for each candidate substitute
- actions to select a candidate, explicitly confirm it, or revert an accepted
  decision
- a clear notice that the recipe itself is unchanged
- an omission action (defined below) and an explicit confirmation

An accepted choice should be visible on the week card without making the card
busy: e.g. a count badge, “1 substitution”, linking to the pre-cooking page.
The page should still be usable when JavaScript is unavailable.

### B. Kitchen Mode

Kitchen Mode consumes the same effective planned ingredient list. It must show
the original wording plus the planned change, rather than hiding the recipe
line. Example:

`500 g spaghetti — use 500 g tagliatelle instead`

Kitchen Mode may provide the same substitute/revert actions if the meal is not
yet cooked, but it must not become the only management surface. The
pre-cooking page is the primary place to review choices before shopping or
cooking.

### C. Shopping calculation

Shopping calculation consumes the effective planned ingredient list. It does
not become a second substitution-management surface in the first version.
For each effective ingredient:

- `available` contributes nothing to restocking;
- `unknown` and `unavailable` contribute to calculated shopping unless the
  existing shopping policy says otherwise;
- an existing shopping-list item indicates restocking is planned, but does not
  change the pantry state;
- a substitute decision removes the original slot contribution and adds the
  substitute contribution;
- an omission contributes nothing.

If an original ingredient is `unavailable` and already on the shopping list,
the planning page should explain that substitution can avoid that restocking
need. Removing the contribution must remain slot-aware so other meals can
continue to require the original.

Do not offer substitution actions for aggregated or manual shopping items in
the first version.

The planning page is the only suggestion surface initially. Adding a meal must
not show a prompt or apply a decision automatically.

## Data model

Keep this data in `planning` because decisions apply to one meal slot. Reuse
the existing canonical-ingredient similarity and pantry services rather than
creating a substitution-specific rule system in the first version.

### `MealSlotIngredientDecision`

The immutable-enough record of an accepted planned-session choice:

| Field | Notes |
| --- | --- |
| `id` | UUID primary key |
| `meal_slot` | FK to the target `MealSlot` |
| `recipe_ingredient` | FK to the original `RecipeIngredient` |
| `decision_type` | `substitute` or `omit` |
| `substitute_ingredient` | Required for `substitute`, null for `omit` |
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
- substitute belongs to the household
- omission has no substitute ingredient and no invented canonical ingredient

The decision must capture the original line by ID rather than only canonical
ingredient ID: a recipe can contain repeated ingredients with distinct units,
amounts, or preparation text.

### Candidate ranking

Do not add an opaque confidence model or automatically create reusable
substitution rules. For the first delivery, derive “previously used” candidates from
accepted, non-reverted decisions and combine them with the existing canonical
similarity results. Rank candidates in this order:

1. previously accepted and similar;
2. previously accepted;
3. similar canonical ingredients.

Within each group, prefer `available`, then `unknown`, then `unavailable`.
Show `on shopping list` separately as “restocking planned”; it never counts as
`available`. Every candidate still requires explicit acceptance for the
current meal.

## Effective ingredient resolution

Create one service-level resolver used by the planning page, Kitchen Mode,
shopping aggregation, and APIs. Do not duplicate this logic in views/templates.

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
- Inventory exclusion is evaluated against the **effective** ingredient. Only
  `available` excludes a contribution. `unknown` and `unavailable` still
  require restocking unless an existing shopping item already represents that
  need.
- A shopping-list entry is restocking intent, not pantry availability. It must
  not change `unknown` or `unavailable` to `available`.

### Provenance

Preserve enough existing recipe/slot provenance to update only the affected
planned meal contribution. A dedicated shopping-side substitution UI and
component-level provenance are deferred; do not make aggregated shopping items
responsible for accepting substitutions.

### Regeneration semantics

Existing behavior preserves purchased, skipped, and manual items on
regeneration. Maintain it:

- A new accepted/reverted decision affects only calculated, still-open items.
- Do not silently alter a purchased or skipped item; retain it as a historical
  shopping decision.
- Tell the user when the list has been recalculated or is now out of date.
- Prefer the existing explicit shopping-list refresh policy after a planning
  decision. Document that policy in UI/API tests; do not silently change
  purchased, skipped, or manual items.

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

Omission is recorded for the current meal only. Reusing omission history as a
suggestion is deferred until the basic planning flow has proven useful.

## UI details and accessibility

- Use server-rendered forms with POST endpoints, CSRF protection, and visible
  success/error messages; JavaScript may enhance confirmation dialogs.
- Confirmation controls must identify original ingredient, substitute/omit
  choice, meal title/date, pantry state, restocking status, and shopping
  consequence.
- Revert controls require confirmation when an active shopping list has
  calculated entries affected by the decision.
- Do not rely on color to distinguish substituted, omitted, or original
  ingredients. Include readable text and status labels.
- On the week card, provide an accessible label such as “Review ingredients
  for Sugo; one substitution selected.”
- Do not expose candidate history or plan references across household
  boundaries.

## Service and API work

Add service operations rather than embedding writes in page views:

- list similar and previously accepted candidates for one slot ingredient
- resolve effective ingredient lines for a slot
- accept substitute decision
- accept omission decision
- revert/supersede a decision
- rank candidates without applying them

All mutation operations need `transaction.atomic`, household filtering, row
locks as appropriate, and optimistic concurrency. Check the meal slot version
provided by the caller; increment it when effective planned ingredients
change, because its shopping/cooking representation changed.

Suggested endpoints:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/meal-slots/{slotId}/ingredients` | Original/effective lines, decisions, candidates, pantry state, restocking status |
| `POST` | `/api/v1/meal-slots/{slotId}/ingredient-decisions` | Explicit substitute/omit acceptance with slot version |
| `DELETE` | `/api/v1/meal-slots/{slotId}/ingredient-decisions/{id}` | Revert an active decision with slot version |

Expose accepted effective-line data in the meal-plan API so non-HTML clients
do not recompute it. Return `409 stale_version` with the current slot version
for conflicts, matching existing plan behavior.

## Recommended implementation order

1. Verify and reuse canonical-ingredient similarity, pantry-state, and
   shopping-list services.
2. Add `MealSlotIngredientDecision`, migrations, household validation, and
   accept/omit/revert services.
3. Implement the effective-ingredient resolver and tests for recipe-line
   identity, cooked slots, and recipe immutability.
4. Build the optional server-rendered planning page and week-card entry point.
   Show `available`, `unknown`, or `unavailable` separately from
   `on shopping list` (“restocking planned”).
5. Render the same effective lines in Kitchen Mode.
6. Update shopping aggregation to consume effective lines and apply the
   existing refresh policy without changing pantry state or purchased,
   skipped, or manual items.
7. Add history/similarity candidate ranking and focused UI/API tests. Every
   candidate remains an explicit per-meal decision.
8. Update the API specification, domain model, product requirements, and
   backlog status once behavior is delivered.

## Cross-feature dependencies and risks

| Dependency | Required behavior |
| --- | --- |
| Canonical ingredient similarity | Candidate suggestions require stable canonical IDs and the existing similarity behavior; do not add a substitution-specific ranking system until this is understood. |
| Meal planning | Decisions attach to `MealSlot` + `RecipeIngredient`, use planned servings, respect cooked state, and increment slot versions. |
| Shopping | Must aggregate effective, not original, ingredients. Being on the shopping list indicates restocking intent and never changes pantry state. |
| Pantry | `available` excludes an effective ingredient from restocking; `unknown` and `unavailable` do not. Replacing an ingredient must not mutate its pantry state. |
| Kitchen Mode | Must render original/effective provenance and share the resolver with planning. |
| Inventory/cook events | Cooking does not infer depletion. Any selected depletion action must validate against effective ingredients as well as original recipe lines. |
| Recommendations | Do not add post-add substitution prompts or treat substitutes as recommendation coverage in the initial delivery. |
| Collaboration/concurrency | Slot and shopping updates can race across household members. Reject stale decisions; never overwrite calculated-list states silently. |
| Data retention/history | Keep accepted/reverted decisions available for candidate ranking; do not add rule lifecycle or advanced confidence history in the initial delivery. |

## Acceptance test matrix

At minimum, add service, API, and page tests for:

1. Candidates are offered from canonical similarity and accepted household
   history; no candidate is applied automatically.
2. A user must explicitly accept before the effective line or shopping list
   changes.
3. Acceptance changes only one planned slot and one recipe line; recipe data
   remains byte-for-byte unchanged.
4. Pre-cooking page and Kitchen Mode show the same effective result.
5. Reverting restores the original effective line and, after the selected
   refresh policy, original shopping contribution.
6. Substitute contributions aggregate under tagliatelle, use scaled amounts,
   respect unit rules, and exclude tagliatelle only when it is `available`.
7. `unknown` and `unavailable` are displayed distinctly; neither is treated as
   `available`.
8. An item on the shopping list is displayed as “restocking planned” without
   changing its pantry state.
9. Omission produces no shopping contribution, is visibly labelled, and never
   creates a pantry ingredient.
10. Existing purchased, skipped, and manual shopping rows survive regeneration.
11. Cooked, note, leftovers, foreign-household, mismatched-line, and
    stale-version requests are rejected.
12. Candidate ranking uses accepted decisions only; it does not react to
    dismissals or unaccepted candidates.

## Deferred decisions

Do not include these in the initial implementation without a separate product
decision:

- quantity/ratio conversions for substitutes
- automatic creation of rules from decisions
- household-authored reusable substitution rules
- global, AI-generated, dietary, medical, or allergen substitution data
- transitive substitutions and cycle ranking
- applying substitutions directly to manual shopping items
- substitution actions from aggregated shopping items
- treating substitutes as full recommendation coverage
- post-add suggestion prompts
- multi-slot bulk acceptance without reviewing each affected meal
