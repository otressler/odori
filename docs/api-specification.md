# API specification

## Conventions

The application uses same-origin HTTPS endpoints under `/api/v1`. Browser sessions authenticate requests; all state-changing requests require CSRF protection. IDs are opaque UUIDs. JSON errors use:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "A human-readable explanation.",
    "fields": { "sourceUrl": "Must use HTTPS." }
  }
}
```

Use `201` for creation, `202` for accepted asynchronous work, `204` for successful no-content mutations, `400` for malformed input, `401/403` for identity/authorization, `404` for inaccessible resources, `409` for state conflicts, `413` for oversized uploads, `422` for validation, and `429` for rate limits.

## Endpoint groups

| Method and path | Purpose |
| --- | --- |
| `GET/POST /recipes` | List/search recipes; create a manual draft. |
| `GET/PATCH/DELETE /recipes/{id}` | Read, edit, or archive a recipe. |
| `POST /recipes/{id}/approve` | Publish a reviewed draft to the catalog. |
| `POST /recipes/{id}/favorite` | Toggle the current user's favorite marker. |
| `GET/POST /ingredients` | Search canonical tags; create a reviewed tag when permitted. |
| `GET/PATCH /ingredients/{id}` | Read or update a canonical ingredient. |
| `GET /ingredients/category-scores` | Return ingredient-category classification diagnostics. |
| `GET/PATCH /inventory` | List and batch-update availability statuses. |
| `POST /inventory/{ingredientId}/change-status` | Request or confirm an availability status change; returns planned-meal conflicts when confirmation is needed. |
| `GET/PUT /meal-plans/{weekStart}` | Retrieve a week plan, creating it when absent. `PUT` currently has the same behavior as `GET`; replacement is not implemented. |
| `POST /meal-plans/{weekStart}/slots` | Create a planned meal. |
| `PATCH/DELETE /meal-slots/{id}` | Move, update, or remove a planned meal. |
| `POST /meal-plans/{weekStart}/shopping-lists` | Generate/refresh a list from the plan. |
| `GET /shopping-lists` | List shopping lists. |
| `GET /shopping-lists/{id}` | Retrieve a shopping list and its items. |
| `POST /shopping-lists/{id}/items` | Add a manual entry. |
| `PATCH/DELETE /shopping-lists/{id}/items/{itemId}` | Alter state or remove an entry. |
| `POST /shopping-lists/{id}/items/{itemId}/purchase` | Mark purchased and update inventory atomically. |
| `GET /recommendations` | Return ranked catalog suggestions. |
| `POST /recommendation-outcomes` | Record a local recommendation outcome. |
| `POST /generated-recipe-drafts` | Queue an explicit generated-draft request. |
| `GET /generated-recipe-drafts/{requestId}` | Poll generated-draft status. |
| `POST /meal-slots/{id}/mark-cooked` | Record a cook event. |
| `GET /cook-events` | Retrieve the 100 most recent cook events. |

## Planned-stock confirmation

An inventory status change that could remove an ingredient from an upcoming plan is a two-step action. The initial request:

```json
{ "status": "needs_replenishment", "version": 7 }
```

returns `409` with `error.code: "planned_ingredient_in_use"` and affected meal slots unless it includes `confirmPlannedUse: true`. The confirmation is an explicit user decision and is written to inventory history. `POST /meal-slots/{id}/mark-cooked` performs the linked `cook_recipe` inventory update internally and never requests this confirmation.

## Planned API surfaces

Recipe URL/file imports and real-time WebSocket events are planned capabilities, not current API
surfaces. Current clients read fresh state through the HTTP endpoints above. The eventual import
and collaboration contracts remain in the product requirements and implementation plan until their
routes, durable job models, and transport are implemented.

## Recommendation response

```json
{
  "inventorySnapshotAt": "2026-07-18T17:00:00Z",
  "suggestions": [
    {
      "recipeId": "recipe_uuid",
      "title": "Pasta al Pomodoro",
      "matchedIngredients": ["tomato", "pasta"],
      "missingIngredients": ["basil"],
      "reasons": ["Uses 4 ingredients marked in stock", "Not cooked in the last 21 days"],
      "score": 0.84
    }
  ]
}
```

`score` is ranking metadata, not a statement of nutritional suitability. A generated recipe uses the same recipe-draft schema but has `status: "draft"` and `source.type: "generated"`.

`GET /api/v1/recommendations` returns catalog suggestions only and includes `runId` and
`scoringVersion` in addition to the response above. `POST /api/v1/recommendation-outcomes`
records one local outcome (`opened`, `planned`, `cooked`, `dismissed`, or `hidden`) for a
household-owned recipe; dismissal reasons use a small fixed vocabulary. `POST
/api/v1/generated-recipe-drafts` validates and queues an explicit generation request, returning
`202`, a request ID, and a status URL. `GET /api/v1/generated-recipe-drafts/{requestId}` returns
the queued, running, succeeded, or failed state and includes the generated draft only after it
succeeds. The worker performs the provider request; safe failures are retained on the request.

## Concurrency

`PATCH` requests for recipes, inventory, meal slots, and shopping items include an entity version.
Recipe and shopping-item deletes also include a version. A stale update returns `409` with the
current version. HTTP `If-Match` ETags are not currently supported.

## Meal-slot and cooking semantics

A meal slot has `entryType: "recipe"`, `"leftovers"`, or `"note"`. Recipe entries require `recipeId` and `servings`; leftovers and notes require display text and do not contribute ingredients to shopping calculation. Only recipe entries can be marked cooked.

Marking a recipe slot cooked records history but does not assume all ingredients are depleted. The client may submit explicit inventory changes chosen during cooking:

```json
{
  "slotVersion": 4,
  "inventoryChanges": [
    {
      "ingredientId": "ingredient_uuid",
      "status": "needs_replenishment",
      "version": 7
    }
  ]
}
```

The server verifies that every changed ingredient belongs to the slot's recipe and applies the cook event and inventory changes atomically. These verified changes use the `cook_recipe` origin and bypass planned-stock confirmation. A client cannot set that origin through the general inventory endpoint. A stale slot or inventory version rejects the entire transaction with `409`.

Calculated shopping items expose `quantityComponents` rather than one fabricated total. Components with compatible normalized units may be summed after serving scaling; unknown amounts and incompatible units remain separate. For example, `1 bunch` and `20 g` of the same canonical ingredient appear on one item as two components because the initial release performs no unit conversion.
