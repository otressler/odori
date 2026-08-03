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
| `POST /recipe-imports` | Start URL or file import; returns `202` and job ID. |
| `GET /recipe-imports/{id}` | Poll import state, stage, retryability, and review draft. |
| `POST /recipe-imports/{id}/retry` | Retry a failed transient import. |
| `GET/POST /ingredients` | Search canonical tags; create a reviewed tag when permitted. |
| `GET/PATCH /inventory` | List and batch-update availability statuses. |
| `POST /inventory/{ingredientId}/change-status` | Request or confirm an availability status change; returns planned-meal conflicts when confirmation is needed. |
| `GET/PUT /meal-plans/{weekStart}` | Retrieve or replace week metadata. |
| `POST/PATCH/DELETE /meal-plans/{weekStart}/slots[/{id}]` | Create, move/update, or remove planned meals. |
| `POST /meal-plans/{weekStart}/shopping-lists` | Generate/refresh a list from the plan. |
| `GET/POST /shopping-lists[/{id}]` | List/create lists and retrieve one list. |
| `POST/PATCH/DELETE /shopping-lists/{id}/items[/{itemId}]` | Add, alter state, or remove entries. |
| `POST /shopping-lists/{id}/items/{itemId}/purchase` | Mark purchased and update inventory atomically. |
| `POST /recommendations` | Return deterministic ranked catalog suggestions. |
| `POST /recommendations/{runId}/feedback` | Record an idempotent recommendation outcome. |
| `POST /meal-slots/{id}/mark-cooked` | Record a cook event. |

## Planned-stock confirmation

An inventory status change that could remove an ingredient from an upcoming plan is a two-step action. The initial request:

```json
{ "status": "needs_replenishment", "version": 7 }
```

returns `409` with `error.code: "planned_ingredient_in_use"` and affected meal slots unless it includes `confirmPlannedUse: true`. The confirmation is an explicit user decision and is written to inventory history. `POST /meal-slots/{id}/mark-cooked` performs the linked `cook_recipe` inventory update internally and never requests this confirmation.

## Real-time events

Connect to `wss://odori.tail-net-name.ts.net/api/v1/realtime` using the authenticated same-origin session. The server assigns household inventory and authorized shopping-list subscriptions; clients do not choose arbitrary channel names.

```json
{
  "type": "shopping.item.updated",
  "resourceId": "shopping_item_uuid",
  "listId": "shopping_list_uuid",
  "version": 12,
  "actor": { "id": "user_uuid", "displayName": "Mara" },
  "changed": { "state": "purchased" }
}
```

Event types include `inventory.item.updated`, `shopping.item.created`, `shopping.item.updated`, `shopping.item.deleted`, and `shopping.list.regenerated`. Events are notifications of committed writes, not commands. On reconnect or a version gap, clients fetch the affected resource through its REST endpoint.

## Import request

URL imports use JSON:

```json
{ "sourceUrl": "https://example.org/recipe" }
```

File imports use `multipart/form-data` with a `file` part. The accepted response contains:

```json
{
  "id": "job_uuid",
  "state": "queued",
  "statusUrl": "/api/v1/recipe-imports/job_uuid"
}
```

The job response includes safe progress data (`queued`, `extracting`, `normalizing`, `awaiting_review`), a structured `draftRecipe` when available, field-level review flags, and a retryable error code. It never exposes provider credentials or raw stack traces.

## Recommendation response

The request is bounded to 20 tags and 20 results:

```json
{
  "weekStart": "2026-07-20",
  "preferredTagIds": ["tag_uuid"],
  "limit": 10
}
```

The response contains a reproducible run and catalog-only suggestions:

```json
{
  "run": {
    "id": "run_uuid",
    "asOf": "2026-07-18T17:00:00Z",
    "targetWeek": "2026-07-20",
    "scoringVersion": "catalog-v1",
    "inventorySnapshotAt": "2026-07-18T16:55:00Z",
    "candidateCount": 42,
    "candidateSetTruncated": false,
    "queryDurationMs": 8,
    "scoringDurationMs": 1
  },
  "suggestions": [
    {
      "recipeId": "recipe_uuid",
      "recipeVersion": 3,
      "title": "Pasta al Pomodoro",
      "scoreBp": 9000,
      "components": {
        "inventoryCoverageBp": 6500,
        "missingPenaltyBp": 0,
        "unknownPenaltyBp": 0,
        "cookRecencyBp": 1500,
        "preferredTagsBp": 1000,
        "alreadyPlannedPenaltyBp": 0,
        "negativeFeedbackPenaltyBp": 0
      },
      "matchedIngredients": [
        {"canonicalIngredientId": "ingredient_uuid", "name": "Tomato", "unresolved": false}
      ],
      "missingIngredients": [],
      "unknownIngredients": [],
      "unresolvedCount": 0,
      "reasons": [{"code": "inventory_coverage"}, {"code": "cook_recency"}]
    }
  ]
}
```

`catalog-v1` uses integer basis points. Inventory coverage contributes up to 6,500,
missing ingredients subtract up to 2,000, unknown ingredients subtract up to 1,000,
cook recency contributes up to 1,500 over 21 local-calendar days, selected tags contribute
up to 1,000, an already-planned recipe subtracts 1,500, and active hidden/`not_again`
feedback subtracts 2,500. The result is clamped to 0–10,000 and ties sort by lexical recipe
UUID. Reason codes have stable order:
`inventory_coverage`, `missing_ingredients`, `unknown_ingredients`, `cook_recency`,
`preferred_tags`, `already_planned`, `negative_feedback`.

Feedback accepts `recipeId`, an outcome (`opened`, `planned`, `cooked`, `dismissed`, or
`hidden`), and an optional bounded reason. It is scoped to the requesting user, household,
run, and a recipe present in that run. `scoreBp` is ranking metadata, not a statement of
nutritional suitability.

## Concurrency

`PATCH` requests for recipe, inventory, meal-slot, and shopping-item changes include an entity version or HTTP `If-Match` ETag. A stale update returns `409` with the current version. This prevents a phone and tablet from silently overwriting each other.

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
