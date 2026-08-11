# Domain model

## Principles

Ingredient identity is separate from the text written in a recipe. A recipe may say “3 cloves of garlic,” while the canonical ingredient is `garlic`. Recipes retain the original text and optional quantity/unit for cooking; pantry and shopping workflows operate on the canonical ingredient.

Inventory deliberately models availability, not stock levels. This avoids false precision and makes recommendations conservative.

## Entities

| Entity | Key fields | Notes |
| --- | --- | --- |
| `household` | id, name, created_at | Ownership boundary for all shared food-planning data. |
| `user` | id, display_name, locale | A person with an authenticated local account. |
| `household_membership` | household_id, user_id, role, joined_at | `owner` or `member`; determines shared-data access. |
| `ingredient_category` | id, household_id, name, sort_order | Examples: produce, dairy, pantry, spice. Seeded defaults are copied into a household so they remain locally editable. |
| `canonical_ingredient` | id, household_id, name, category_id, aliases, icon, icon_status, active | Household-scoped stable identity shared by recipes, inventory, and lists. |
| `recipe` | id, household_id, title, status, servings, source_id, created_by, archived_at | Status is `draft`, `approved`, or `archived`. |
| `recipe_ingredient` | id, recipe_id, canonical_ingredient_id, source_text, amount, unit, optional, sort_order, match_state | Amount/unit may be absent; `match_state` supports review. |
| `recipe_step` | id, recipe_id, body, sort_order, timer_seconds | Ordered cooking instruction. |
| `recipe_tag` | id, household_id, name | Household-defined label with a case-insensitive unique name. |
| `recipe_tag_assignment` | recipe_id, tag_id | Many-to-many recipe classification within one household. |
| `recipe_favorite` | recipe_id, user_id, created_at | Per-user favorite marker; unique by recipe and user. |
| `recipe_source` | id, household_id, type, attribution, imported_at | Type is currently `manual` or `generated`. URL/image/PDF source references are planned with assisted import. |
| `recipe_image_job` | id, recipe_id, prompt, state, attempt_count, error_code, correlation_id | Durable recipe-image work; state is `queued`, `running`, `succeeded`, `failed`, or `superseded`. |
| `generated_recipe_request` | id, household_id, recipe_id, idea, state, attempt_count, provider_deployment, error_code | Durable explicit generated-draft request. A successful request links to a draft recipe. |
| `pantry_categorization_job` | id, household_id, state, assigned_count, attempt_count, error_code | Durable household ingredient-category assignment work. |
| `ingredient_icon_job` | id, ingredient_id, prompt, state, attempt_count, available_at, error_code | Durable ingredient-icon work, including a provider rate-limit schedule. |
| `inventory_item` | id, household_id, canonical_ingredient_id, status, version, updated_at | One active row per household and canonical ingredient. |
| `inventory_event` | id, item_id, previous_status, new_status, actor_id, origin, meal_slot_id | Append-only audit record. `origin` includes `manual`, `purchase`, and `cook_recipe`. |
| `meal_plan` | id, household_id, week_start_date | Week starts on the locale-configured first day. |
| `meal_slot` | id, plan_id, date, slot, entry_type, recipe_id, servings, notes, cooked_at | `slot`: breakfast, lunch, dinner, or snack. `entry_type`: `recipe`, `leftovers`, or `note`; only `recipe` requires recipe/servings and can be marked cooked. |
| `shopping_list` | id, household_id, name, plan_id, state, version, generated_at | State is `active`, `completed`, or `archived`. |
| `shopping_item` | id, list_id, canonical_ingredient_id, label, quantity_components, source, state, recipe_refs, version | `source`: calculated/manual; `state`: open/purchased/skipped. `quantity_components` preserves compatible/incompatible amount-unit groups without pretending to convert units. |
| `cook_event` | id, household_id, recipe_id, meal_slot_id, cooked_at, actor_id | Recommendation recency signal; created when a slot is marked cooked. |
| `recommendation_run` | id, household_id, requested_by, inventory_snapshot_at, input_snapshot, scoring_version, created_at | Stores reproducibility metadata for a deterministic recommendation response. |
| `recommendation_outcome` | id, household_id, recipe_id, actor_id, run_id, outcome, reason, created_at | Records a household action such as `opened`, `planned`, `cooked`, `dismissed`, or `hidden`. |

## Relationships

```text
recipe_source 1 ── * recipe
recipe 1 ── * recipe_image_job
recipe 0..1 ── * generated_recipe_request
recipe 1 ── * recipe_ingredient ── 1 canonical_ingredient ── 1 ingredient_category
recipe 1 ── * recipe_step
recipe * ── * recipe_tag
canonical_ingredient 1 ── 0..1 inventory_item ── * inventory_event
canonical_ingredient 1 ── * ingredient_icon_job
household 1 ── * household_membership ── 1 user
household 1 ── * recipe
household 1 ── * canonical_ingredient
household 1 ── * inventory_item
meal_plan 1 ── * meal_slot ── 1 recipe
shopping_list 1 ── * shopping_item ── 0..1 canonical_ingredient
meal_slot 0..1 ── 1 cook_event
```

## Inventory state rules

| Status | Meaning | Shopping default |
| --- | --- | --- |
| `in_stock` | Enough is likely available for normal use. | Exclude from calculated list. |
| `needs_replenishment` | Present but likely insufficient or desired soon. | Include. |
| `unknown` | Availability is not known. | Include, visibly marked for confirmation. |

There is no `out_of_stock`: use `needs_replenishment`. Removing an inventory item returns it to `unknown` rather than deleting audit history.

## Lifecycle and integrity rules

- A recipe must have a title and at least one instruction before `approved`; ingredients can be incomplete only while `draft`.
- A canonical ingredient cannot be deleted once referenced; it may be merged or deactivated.
- Generated drafts and manual drafts must not alter an existing approved recipe unless the user explicitly edits it. URL/file import follows the same rule when implemented.
- Rebuilding a list updates only calculated, unpurchased items. Manual, purchased, and skipped entries are retained.
- A purchase creates both a shopping-item state transition and an inventory event in one transaction.
- Before a manual transition from `in_stock` to `needs_replenishment`, calculate all non-cooked upcoming meal slots that use the ingredient. Require explicit confirmation when the set is non-empty.
- Marking a meal cooked does not infer that every recipe ingredient is depleted. The cooking action may include explicit inventory changes selected by the user; only those changes use origin `cook_recipe` and bypass the planned-stock warning.
- A `cook_recipe` transition is valid only in the same transaction as marking the associated recipe meal slot cooked, must reference its meal slot, and may affect only canonical ingredients on that recipe. It does not require a planned-stock warning.
- Marking a meal slot cooked creates at most one cook event; undoing it removes/voids the associated event.
- Week plans are unique per household and week start date.
- Inventory items, recipes, meal plans, meal slots, shopping lists, and shopping items use monotonically changing versions for optimistic concurrency. Versions also support any future collaboration transport.
- Every household-owned record is queried and mutated through its household boundary. Foreign keys must prevent relationships between records belonging to different households.
- A future import source will use content-hash uniqueness scoped by household and processing schema/provider version; the same source may then be reprocessed after a schema change without overwriting an approved recipe.
- Shopping calculation scales numeric amounts when possible and sums only components with the same canonical ingredient and compatible normalized unit. Unknown amounts or incompatible units remain separate visible components on one item; the initial release does not perform unit conversion.
