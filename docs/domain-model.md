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
| `ingredient_category` | id, name, sort_order | Examples: produce, dairy, pantry, spice. |
| `canonical_ingredient` | id, name, category_id, aliases, active | Stable identity shared by recipes, inventory, and lists. |
| `recipe` | id, title, status, servings, source_id, created_by, archived_at | Status is `draft`, `approved`, or `archived`. |
| `recipe_ingredient` | id, recipe_id, canonical_ingredient_id, source_text, amount, unit, optional, sort_order, match_state | Amount/unit may be absent; `match_state` supports review. |
| `recipe_step` | id, recipe_id, body, sort_order, timer_seconds | Ordered cooking instruction. |
| `recipe_source` | id, type, URL/file reference, content_hash, attribution, imported_at | Type is `manual`, `url`, `image`, `pdf`, or `generated`. |
| `import_job` | id, source_id, state, provider, attempts, error_code, raw_result_ref | State is `queued`, `processing`, `awaiting_review`, `completed`, `failed`. |
| `inventory_item` | id, household_id, canonical_ingredient_id, status, version, updated_at | One active row per household and canonical ingredient. |
| `inventory_event` | id, item_id, type, previous_status, new_status, actor_id, origin, meal_slot_id | Append-only audit record. `origin` includes `manual`, `purchase`, and `cook_recipe`. |
| `meal_plan` | id, household_id, week_start_date | Week starts on the locale-configured first day. |
| `meal_slot` | id, plan_id, date, slot, recipe_id, servings, notes, cooked_at | `slot`: breakfast, lunch, dinner, or snack; one recipe per slot initially. |
| `shopping_list` | id, household_id, name, plan_id, state, version, generated_at | State is `active`, `completed`, or `archived`. |
| `shopping_item` | id, list_id, canonical_ingredient_id, label, source, state, recipe_refs, version | `source`: calculated/manual; `state`: open/purchased/skipped. |
| `cook_event` | id, recipe_id, meal_slot_id, cooked_at, actor_id | Recommendation recency signal; created when a slot is marked cooked. |
| `recommendation_run` | id, requested_at, input_snapshot, model_version | Stores reproducibility metadata, not an implicit recipe mutation. |

## Relationships

```text
recipe_source 1 ── * recipe
recipe 1 ── * recipe_ingredient ── 1 canonical_ingredient ── 1 ingredient_category
recipe 1 ── * recipe_step
canonical_ingredient 1 ── 0..1 inventory_item ── * inventory_event
household 1 ── * household_membership ── 1 user
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
- Saving a recipe draft from import must not alter an existing approved recipe unless the user explicitly edits it.
- Rebuilding a list updates only calculated, unpurchased items. Manual, purchased, and skipped entries are retained.
- A purchase creates both a shopping-item state transition and an inventory event in one transaction.
- Before a manual transition from `in_stock` to `needs_replenishment`, calculate all non-cooked upcoming meal slots that use the ingredient. Require explicit confirmation when the set is non-empty.
- A `cook_recipe` transition is valid only while marking the associated meal slot cooked and must reference its meal slot. It does not require a planned-stock warning.
- Marking a meal slot cooked creates at most one cook event; undoing it removes/voids the associated event.
- Week plans are unique per household and week start date.
- Inventory items, meal plans, shopping lists, and shopping items use monotonically changing versions for real-time clients and optimistic concurrency.
