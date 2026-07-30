# Code review — Odori

Review date: 2026-07-29. Reviewed at commit state of the working tree.

**Scope calibration:** this is a self-hosted Django app for one household plus friends, running
behind Tailscale + Traefik on a Raspberry Pi. Findings are ranked with that in mind — "scale",
"multi-tenancy hardening", and "enterprise ops" concerns are deliberately downgraded or dropped.
What stays high is: things that are broken, things that cost money, and things that make the app
hard to change later.

Use the checkboxes to work through this incrementally.

---

## 1. Bugs (broken today)

### [x] 1.1 Pantry search crashes — `AttributeError` on every `?q=` request

[pantry/views.py](pantry/views.py#L78) filters a `list` with a queryset method:

```python
items = list(InventoryItem.objects...order_by(...))   # already a list
if query:
    items = items.filter(ingredient__name__icontains=query)   # list has no .filter
```

The search box that triggers it is [templates/pantry/inventory.html](templates/pantry/inventory.html#L40).
The recipe list has the equivalent search and works, so this is an isolated regression.

Fix: apply `.filter(...)` before materialising the list, or filter in Python like the status filter
just below it. Add a test — `pantry/tests.py` covers the inventory page but never passes `q`.

### [x] 1.2 Ingredient icons 404 in production

[templates/shopping/_item.html](templates/shopping/_item.html#L10) renders
`{{ item.canonical_ingredient.icon.url }}`, which resolves to `/media/pantry-icons/...`.
Media is only routed when `DEBUG` is true ([odori/urls.py](odori/urls.py#L47)), and WhiteNoise
serves `STATIC_ROOT` only. So every shopping-list icon the worker generates is invisible in the
deployed app — while still costing an image-generation call.

Recipe images already solve this correctly with an authenticated view (`recipe-image` in
[recipes/views.py](recipes/views.py)). Mirror that for icons, or drop the icon feature. Serving
`/media/` unauthenticated is the worse option since it would expose household data by URL guess.

### [x] 1.3 Missing `operation` on one image diagnostic path

In [recipes/images.py](recipes/images.py), the `base64.b64decode` failure branch calls
`_record_image_diagnostic(...)` without `operation=operation`, so a corrupt *ingredient icon*
response is recorded as `recipe_image_generation`. One-line fix; matters because the operations
page groups by operation.

---

## 2. Security

### [x] 2.1 State-changing views reachable via GET (pantry + recipes)

`planning/urls.py` and `shopping/urls.py` correctly wrap mutating views in `require_POST`.
[recipes/urls.py](recipes/urls.py) and [pantry/urls.py](pantry/urls.py) do not. That means these
are all reachable by a plain `GET`, which bypasses CSRF entirely:

| Route | Effect of a GET |
| --- | --- |
| `recipes/<id>/archive/` | archives the recipe |
| `recipes/<id>/approve/` | publishes a draft |
| `recipes/<id>/favorite/` | toggles favourite |
| `recipes/<id>/revise/` | creates a draft copy |
| `recipes/<id>/image/regenerate/` | **triggers a paid image generation** |
| `recipes/<id>/ingredients/<id>/add-to-pantry/` | creates an ingredient + inventory item |
| `pantry/categories/suggest/` | queues a categorisation job |

Trigger is as cheap as an `<img src="https://odori.../recipes/<id>/image/regenerate/">` in any page
a logged-in family member opens, or an over-eager browser/link prefetcher. Tailscale limits *who*
can reach the host, not *what a logged-in browser is tricked into requesting*.

Fix: wrap them in `require_POST` in the URLconf, exactly as the other two apps do. Confirm the
templates already POST (they do — they all use `<form method="post">`).

### [x] 2.2 No password validators

`AUTH_PASSWORD_VALIDATORS` is absent from [odori/settings.py](odori/settings.py), so
`bootstrap_owner` and `set_user_password` will happily accept `a`. Add Django's default four
validators (or at least `MinimumLengthValidator`); costs nothing.

### [x] 2.3 `DEBUG` defaults to `true`

[odori/settings.py](odori/settings.py#L13): `os.environ.get("DEBUG", "true")`. Compose passes an
empty string in production so it currently resolves safely, but any `docker run` without the var
gets a debug-mode app with tracebacks and `/media/` wide open. Flip the default to `false` and make
local dev opt in.

### [x] 2.4 `SECRET_KEY` has a hardcoded fallback

[odori/settings.py](odori/settings.py#L12) falls back to `"development-only-change-me"`. Same shape
of risk as 2.3. Consider `raise ImproperlyConfigured` when `not DEBUG and SESSION_SECRET is unset`.

### [x] 2.5 No transport/security headers

No `SECURE_HSTS_SECONDS`, `SECURE_SSL_REDIRECT`, `SECURE_PROXY_SSL_HEADER`, or
`SECURE_CONTENT_TYPE_NOSNIFF`. Traefik terminates TLS, so at minimum set
`SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")` — otherwise `request.is_secure()`
is false behind the proxy and secure-cookie behaviour is subtly wrong. `python manage.py check
--deploy` will list the rest.

### [x] 2.6 Unvalidated field assignment in the ingredient PATCH API

[pantry/api.py](pantry/api.py) `ingredient_detail` does `setattr(ingredient, key, data[key])` for
`name`/`aliases`/`active` with no type or length checking — `aliases` can be set to a string, an
int, or a 10 MB nested structure that later breaks `rank_ingredients`. Validate the three fields
explicitly.

### [x] 2.7 No upload/body size limits

`DATA_UPLOAD_MAX_MEMORY_SIZE` / `DATA_UPLOAD_MAX_NUMBER_FIELDS` are left at defaults. Django's
defaults are sane, so this is low priority — but the recipe form builds arbitrarily many
`ingredient-source-N` fields, so a cheap explicit cap on ingredient/step count in
`recipe_form_data` is worth having.

---

## 3. Cost and performance (Azure spend)

This is where a family app actually gets hurt, because the failure mode is a bill.

### [ ] 3.1 Every recipe save regenerates the image

[recipes/services.py](recipes/services.py#L88): `create_or_update_recipe` unconditionally calls
`queue_recipe_image(recipe)`, which deletes the current image and enqueues a new generation.
Fixing a typo in the description throws away a good image and pays for a new one.

Fix: Only re-queue on creation and when the recipe has no image. Images are expensive and slow to generate, so the UX should be "click regenerate" rather than "save and wait for a new one".

### [ ] 3.2 Embedding calls happen synchronously inside request handling

- [recipes/semantic.py](recipes/semantic.py#L12) `update_search_embedding` is called from inside
  `create_or_update_recipe`'s `@transaction.atomic` block — a 5-second-timeout HTTP call while
  holding row locks.
- `rank_ingredients` and `rank_recipes` call `embed(query)` on **every** search keystroke/submit,
  with no caching.

Fix: move embedding into the existing job/worker path (you already have durable jobs), and/or
memoise query embeddings. At minimum, move the call outside the transaction.

### [ ] 3.3 `similar_ingredient_recommendations` is O(n) network calls per page view

[pantry/services.py](pantry/services.py) loops over every active ingredient and calls
`rank_ingredients(retained, ingredient.name)`, each of which calls `embed()`. Opening
`/pantry/condense/` with 200 ingredients = 200 embedding requests. Compute the pairwise similarity
from the already-persisted `ingredient.embedding` vectors instead of re-embedding names.

### [ ] 3.4 Icon queueing on a page render, and cross-household on worker start

- [shopping/views.py](shopping/views.py#L75) calls `queue_missing_ingredient_icons(...)` during a
  plain `GET` of the shopping list — writes rows and enqueues paid work as a side effect of reading.
- [core/management/commands/worker.py](core/management/commands/worker.py#L90) queues icons for
  `CanonicalIngredient.objects.filter(active=True)` across *all* households on every worker boot,
  unbounded. A crash-loop turns into a spend-loop.

Fix: move icon backfill behind an explicit management command or an owner-triggered action, and
cap how many are queued per run.

### [ ] 3.5 `time.sleep()` in the icon job blocks the whole worker

[pantry/images.py](pantry/images.py) sleeps `AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS` (default 12 s)
inside `run_next_ingredient_icon_job`, which stalls category and recipe-image jobs too. Prefer a
`next_run_at`/`available_at` column, or track the last provider call timestamp and skip the runner
instead of sleeping. Low priority for one worker, but it makes the queue feel dead.

### [ ] 3.6 Missing indexes on the hot filters

Most models are only queried by `household` + one field, but there are no `Meta.indexes` on
`Recipe(household, status)`, `InventoryItem(household, status)`, `MealSlot(plan, date)`, or
`ShoppingItem(shopping_list, state)`. Not urgent at family scale on SQLite/Postgres, but they're
free to add and the queries are already written.

---

## 4. Correctness / robustness

### [x] 4.1 Batch inventory PATCH is not atomic

[pantry/api.py](pantry/api.py) `inventory` PATCH iterates `items`, applying each change and
returning early on the first invalid one — earlier changes are already committed. Wrap the loop in
`transaction.atomic` or validate the whole batch before writing.

### [x] 4.2 `create_or_update_recipe` bypasses `household_for`

[recipes/services.py](recipes/services.py) uses
`user.memberships.select_related("household").first().household`, which raises `AttributeError`
(500) instead of the `Http404` the rest of the codebase produces for a user with no membership. Use
`core.services.household_for`.

### [x] 4.3 DELETE endpoints ignore optimistic-concurrency versions

`recipe_detail` DELETE (archive) and `shopping_item_detail` DELETE both skip the `version` check
that every other mutation enforces. Either check it or document that deletes are last-write-wins.

### [x] 4.4 `home` mutates on GET

`get_or_create_plan` in the home view creates a `MealPlan` row on a read request. Harmless, but it
means a bot/prefetch populates your DB and it breaks the "GET is safe" rule the rest of the review
leans on.

### [x] 4.5 `.env` loading is inconsistent

[odori/settings.py](odori/settings.py) reads `.env` via `dotenv_values` but only promotes keys
starting with `AZURE_OPENAI_`. Running `manage.py` locally therefore silently ignores `DEBUG`,
`DATABASE_URL`, `SESSION_SECRET` from the same file. Either load all of it or drop the special case
and rely on `env_file` in Compose.

---

## 5. Design and maintainability

### [ ] 5.1 Cross-app private imports

[pantry/images.py](pantry/images.py) imports `_generate_image_bytes` and
`RecipeImageGenerationError` from `recipes.images`. A leading underscore says "don't", and pantry
depending on recipes for image plumbing is backwards. Extract a shared
`providers/foundry_images.py` (the `providers/` package already exists and is nearly empty) and have
both apps depend on it.

### [ ] 5.2 Duplicated JSON API helpers

`payload`/`read_json` and two incompatible `error()` signatures live in `pantry/api.py` and
`recipes/api.py`; `shopping/api.py` and `planning/api.py` import them *from pantry*. Move them to a
single `core/api.py`.

### [ ] 5.3 Three near-identical job runners

`run_next_category_job`, `run_next_recipe_image_job`, `run_next_ingredient_icon_job` share the same
~60-line claim/run/succeed/fail shape with slightly different logging and error handling (the icon
runner uniquely swallows generic exceptions; the category runner uniquely omits a `SUPERSEDED`
state). This is the largest duplication in the codebase and the most likely source of future
divergence. Extract a generic `run_next_job(model, handler, job_type)`.

### [ ] 5.4 `job_state_counts` special-cases a model by identity

[core/views.py](core/views.py) does `if model is RecipeImageJob:` to pick the household lookup path.
Give the job models a common `for_household(household)` classmethod/manager instead.

### [ ] 5.5 Models lack `__str__`

None of the domain models define `__str__`, so the Django admin and any shell debugging show
`Recipe object (uuid)`. Cheap quality-of-life win.

### [ ] 5.6 `AuditContext` model appears unused

[core/models.py](core/models.py) defines `AuditContext` but nothing writes to it — `RequestContextMiddleware`
logs to stdout instead, and Orbit covers request telemetry. Delete it or wire it up; a dead table
with a migration is worse than neither.

### [ ] 5.7 Leftover `media/` directory

`MEDIA_ROOT` is `BASE_DIR/data/uploads`, but a `media/recipes/` directory exists in the repo. Looks
like a leftover from an earlier layout — remove it so nobody assumes it's live.

---

## 6. Deployment and docs

### [ ] 6.1 Docs have drifted from the code

[docs/deployment-operations.md](docs/deployment-operations.md) documents required variables that
don't exist anywhere in settings: `AZURE_DOCUMENT_INTELLIGENCE_*`, `AZURE_OPENAI_DEPLOYMENT`,
`UPLOAD_MAX_BYTES`, `IMPORT_WORKER_CONCURRENCY`, `AI_IMPORT_ENABLED`, `AI_GENERATION_ENABLED`,
`AI_DAILY_JOB_LIMIT`, `AI_MAX_INPUT_CHARS`, `AI_MAX_OUTPUT_TOKENS`. It also documents
`command: ./bin/worker` (actual: `python manage.py worker`), `postgres:16-alpine` (actual: `18.4`),
`/var/lib/postgresql/data` (actual: `/var/lib/postgresql`), and a `web` network (actual: `proxy`).

Either trim the doc to what exists, or mark the missing switches explicitly as "planned". As written
it's a trap for future-you.

Worth noting: several of those documented switches (`AI_DAILY_JOB_LIMIT`, `AI_GENERATION_ENABLED`)
are exactly the guardrails section 3 says are missing. The design was right; it just wasn't built.

### [ ] 6.2 No migration step in the deployment path

Neither `docker-compose.yml` nor the image entrypoint runs `manage.py migrate`; the docs list it as
a manual release step. For a Pi you update by hand a few times a year that's defensible, but an
entrypoint that runs `migrate` before `gunicorn` (single web replica, so no race) would remove a
whole class of "the site is 500ing after I pulled" evenings.

### [ ] 6.3 `collectstatic` runs at image build with development settings

The Dockerfile runs `collectstatic` before any runtime env exists, so it builds the manifest under
`DEBUG=true` and the fallback secret key. It works, but it's fragile — if any static handling ever
becomes settings-dependent it will fail silently. Passing explicit env vars on that `RUN` line makes
the intent visible.

### [ ] 6.4 No worker healthcheck in Compose

`/health/worker` exists and is good. The `odori-worker` service has no `healthcheck`, so a wedged
worker looks healthy to Docker. Add one that checks the heartbeat row, or scrape `/health/worker`
from the web container.

---

## 7. Testing

Coverage is genuinely good — ~135 tests, and the important invariants are covered: household
isolation, optimistic-locking conflicts, shopping-list regeneration stability, cook-event
idempotency, provider-failure diagnostics. Notable gaps:

- [ ] Pantry inventory **search** (`?q=`) — would have caught bug 1.1.
- [ ] Icon/media URL rendering under `DEBUG=false` — would have caught bug 1.2.
- [ ] HTTP-method enforcement on pantry/recipe mutations — would have caught 2.1.
- [ ] No `manage.py check --deploy` step in [.github/workflows/ci.yml](.github/workflows/ci.yml).
      Adding it catches 2.2/2.3/2.5 automatically and permanently.

CI itself is solid (migration drift check, ruff, collectstatic, pytest, arm64 build). Consider
pinning `pip install` with a hash-checking lockfile if you want reproducible Pi deploys, though
`requirements*.txt` are already fully pinned, so this is optional.

---

## 8. What's good (don't change it)

Worth writing down so a later refactor doesn't undo it:

- **Household scoping is applied consistently** at the service layer (`household_for` / `scoped`),
  and there are explicit cross-household negative tests in three apps.
- **Optimistic concurrency** (`version` columns + stale-version 409s) is applied coherently across
  inventory, slots, recipes, and shopping items. That's unusually disciplined for a hobby project.
- **Provider failures are non-fatal by design** — `EmbeddingResult` with a `state`/`error_code`
  instead of exceptions, plus text-similarity fallback, means Azure being down degrades rather than
  breaks the app.
- **Durable jobs with crash recovery** (`recover_interrupted_*` requeues `RUNNING` rows on boot) and
  a `SUPERSEDED` state to avoid stale writes.
- **Correlation IDs** flow request → job → provider diagnostic, and logs are structured JSON with a
  redaction pass. Orbit is masked by default.
- **Domain logic lives in `services.py`, not views** — both the HTML views and the JSON API call the
  same functions, so behaviour can't diverge between them.
- **No `|safe`, no `mark_safe`, no `csrf_exempt`, no raw SQL** anywhere in the codebase.

---

## Suggested order of work

1. 1.1, 1.2 — user-visible breakage.
2. 2.1 — one-line-per-route fix, removes the CSRF gap.
3. 3.1, 3.3, 3.4 — stop the money leaks.
4. 2.2, 2.3, 2.4, 2.5 + the `check --deploy` CI step — a single settings pass.
5. 4.x — correctness cleanups.
6. 6.1 — reconcile the docs, then decide which "planned" AI guardrails you actually want.
7. 5.1, 5.2, 5.3 — refactors, only once the above stops moving.
