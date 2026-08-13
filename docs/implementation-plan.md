# Implementation plan

## Purpose

This plan turns the product, domain, API, and operations specifications into independently assignable work packets. It assumes a small household deployment, one production Raspberry Pi 5, and no requirement to scale beyond a handful of concurrent users. Correctness, recoverability, and low operating cost take precedence over horizontal scale.

Implementation agents must treat the following documents as contracts:

- [Product requirements](product-requirements.md) defines behavior and acceptance criteria.
- [Architecture](architecture.md) defines ownership and integration boundaries.
- [Domain model](domain-model.md) defines persistence and lifecycle rules.
- [API specification](api-specification.md) defines browser/server contracts.
- [Deployment and operations](deployment-operations.md) defines the production environment.
- [Product backlog](backlog.md) contains candidate work that is not committed until promoted through its decision checklist.
- [Ingredient substitutions handoff](ingredient-substitutions.md) defines the
  post-core substitution work packet and its mandatory planning, shopping, and
  pantry integration.

When implementation reveals an ambiguity, update the relevant contract in the same change. Do not silently invent a conflicting rule in code.

## Delivery strategy

Build one vertical slice at a time. Each milestone must be deployable to the Pi, preserve data from the previous milestone, and include its own migrations, tests, operator notes, and accessible UI states. AI is deliberately late in the sequence: the useful catalog, pantry, planner, and shopping flows must work without Azure.

| Milestone | Usable outcome | Main requirements | Azure dependency |
| --- | --- | --- | --- |
| 0. Engineering foundation | Repeatable local/CI/Pi build with authentication and diagnostics | FR-11 | None |
| 1. Cookbook and pantry | Household can manually curate recipes and track availability | FR-04, FR-05, FR-14 | None |
| 2. Plan, shop, and cook | Weekly planning through purchase and cooking works end to end | FR-07 to FR-10, FR-12, FR-15, FR-16 | None |
| 2.5. Catalog enrichment and operations | Household can categorize its pantry with reviewable suggestions, use generated ingredient artwork, and operate the background work safely | FR-05, FR-11 | Optional Azure OpenAI embeddings and Microsoft Foundry image generation |
| 3. Assisted import | URL, image, and PDF sources become reviewable drafts | FR-01 to FR-03 | Document Intelligence and Azure OpenAI |
| 4. Recommendations | Explainable catalog ranking and optional generated drafts | FR-06, FR-17 | None for ranking; optional Azure OpenAI for generation |
| 5. Collaboration and recovery | Multi-device updates, conflict handling, export, and tested recovery | FR-13, FR-18 | None |

Milestones 0 through 2 form the minimum useful product. Stop there temporarily if cloud cost, implementation time, or AI quality is unsatisfactory.

## Cross-cutting constraints

### Resource envelope

Use these as engineering budgets, not optimistic targets:

- Production must run on an ARM64 Raspberry Pi 5 with 8 GB RAM; leave at least 2 GB for the OS, Traefik, Tailscale, Portainer, and filesystem cache.
- The web container should remain below 750 MB resident memory during normal use, the worker below 1 GB, and PostgreSQL below 1.5 GB. Validate with representative imports and concurrent browser sessions.
- Run one web process and one worker process initially. Limit the worker to one AI/import job at a time and make concurrency configurable.
- Typical non-AI requests should not require Azure and should remain usable during an Azure outage.
- Do not add Redis, a message broker, Kubernetes, or a distributed cache for the initial household deployment.
- Store uploads and PostgreSQL data on durable storage. Avoid sustained write-heavy logs on the Pi's boot media.

### Cost envelope

The hard operating target is less than USD 50 per month, with a normal target below USD 15 excluding hardware and home internet. Provider pricing varies by region and model, so deployment configuration must use current regional prices rather than values copied into code.

| Cost area | Normal target | Hard guardrail |
| --- | ---: | ---: |
| Pi hosting | Existing household electricity only | No paid always-on compute |
| Azure Document Intelligence | Under USD 5/month | Monthly budget alert and per-household page quota |
| Azure OpenAI | Under USD 8/month | Token/job limits and generated-recipes feature switch |
| Azure Functions Flex Consumption, if enabled | Near zero at household traffic | No always-ready instances; monthly budget alert |
| Backup storage, if off-device cloud storage is chosen | Under USD 2/month | Retention and lifecycle policy |
| Total Azure spend | Under USD 15/month normally | Alerts at USD 20 and USD 35; disable optional AI before USD 50 |

Enforce limits in the application as well as in Azure:

- Cap upload bytes, PDF pages, URL response bytes, model input characters, output tokens, retries, and jobs per household per day.
- Cache extraction/normalization by content hash and schema version so retries do not repeat billable calls.
- Show a clear `cloud_budget_exceeded` or `ai_temporarily_disabled` result; never make basic planning depend on an AI call.
- Configure Azure Cost Management alerts. An alert is not a spending cap, so expose feature switches that can disable document import and recipe generation independently.
- Record provider, region, model/deployment, billed-unit metadata when available, and estimated usage per job without logging source content.

### Definition of done for every work packet

An agent may mark a packet complete only when:

1. The named behavior is implemented through the owning module rather than through cross-module table writes.
2. Database migrations are forward-applicable and the development seed path still works.
3. Unit or integration tests cover the acceptance criteria and important failure paths.
4. Authorization is tested for both permitted and forbidden household access.
5. UI changes work with keyboard and touch controls at phone and desktop widths.
6. Logs exclude credentials, session values, recipe source content, and unnecessary household data.
7. Relevant documentation and configuration examples are updated.
8. The milestone's focused test command and the repository-wide verification command pass on ARM64-compatible dependencies.

## Milestone 0: Engineering foundation

**Exit gate:** A developer can create a household owner, sign in, reach an authenticated empty shell, run migrations and tests, build an ARM64 image, and deploy it through the documented Compose topology. Restarting containers preserves sessions as designed and all database data.

### Packet 0A: Stack decision and repository scaffold

**Owner:** platform agent  
**Depends on:** none  
**Can run with:** nothing; this packet establishes conventions for all others

Tasks:

- Record an ADR selecting the application framework, migration tool, test framework, CSS/build pipeline, and browser test tool.
- Confirm support for server-rendered pages, JSON endpoints, secure cookie sessions, CSRF, PostgreSQL, WebSockets, a separately invoked worker, and Linux ARM64 images.
- Scaffold `web`, `worker`, domain/application modules, provider adapters, tests, migrations, and static assets without creating network microservices.
- Add deterministic developer commands for format, lint, unit tests, integration tests, browser tests, migration, seed, and container build.
- Add a CI workflow that runs static checks and tests and builds, but does not publish from untrusted pull requests.

Evidence:

- ADR with one selected stack and rejected alternatives.
- CI passes on a minimal authenticated health page.
- `linux/arm64` image starts and responds to health checks.

### Packet 0B: Database, identity, and household boundary

**Owner:** identity/data agent  
**Depends on:** 0A

Tasks:

- Implement `household`, `user`, and `household_membership` migrations and repositories.
- Provide an explicit one-time owner bootstrap command; do not ship default credentials or public registration.
- Implement password hashing using the framework's maintained password hasher, session rotation on sign-in, logout, idle/absolute expiry, secure cookie settings, and CSRF protection.
- Centralize household scoping and return `404` for another household's resources where the API contract requires inaccessible resources to be hidden.
- Add actor and correlation context for audit records and structured logs.

Evidence:

- Integration tests prove cross-household reads and writes fail.
- Session and CSRF tests cover valid, expired, missing, and replayed requests.

### Packet 0C: Runtime, diagnostics, and release skeleton

**Owner:** operations agent  
**Depends on:** 0A  
**Can run with:** 0B

Tasks:

- Add readiness and liveness endpoints; readiness checks required dependencies without exposing details.
- Implement structured logging, redaction, request IDs, job IDs, and bounded log retention.
- Complete development and production Compose files with internal networking, health checks, persistent volumes, resource limits, and graceful shutdown.
- Add migration and owner-bootstrap release commands, immutable image tagging, and an ARM64 build path.
- Add a smoke script that checks HTTPS routing, authentication redirect, database readiness, and volume persistence.

Evidence:

- The documented Pi deployment and rollback rehearsal succeeds.
- Stopping web or worker during a request/job does not corrupt committed data.

## Milestone 1: Cookbook and pantry

**Exit gate:** A household can create, review, approve, search, edit, favorite/tag, archive, scale, and cook from manually entered recipes. It can maintain canonical ingredients and coarse inventory without any Azure configuration.

### Packet 1A: Ingredient taxonomy and pantry domain

**Owner:** pantry agent  
**Depends on:** 0B

Tasks:

- Implement ingredient categories, canonical ingredients, aliases, deactivation/merge rules, inventory items, and append-only inventory events.
- Enforce one inventory item per household/canonical ingredient and the three-state availability model.
- Implement ingredient search/create and inventory list/batch update APIs with optimistic concurrency.
- Build touch-friendly inventory filtering and status controls. Do not represent availability as a numeric quantity.

Tests:

- State transitions create exactly one audit event.
- Rename/deactivate does not break references; merge behavior is transactional.
- Stale versions and cross-household IDs return the specified conflict/not-found responses.

### Packet 1B: Recipe authoring and lifecycle

**Owner:** recipes agent  
**Depends on:** 0B; coordinate canonical-ingredient contract with 1A  
**Can run with:** 1A using agreed IDs and fixtures

Tasks:

- Implement recipe, source, ingredient-line, step, and tag persistence with draft/approved/archived lifecycle rules.
- Implement manual draft creation, editing, approval, archive, detail, scaling, search, favorite, and tag flows.
- Preserve each ingredient's source text while linking to a stable canonical ID; scaling affects display amounts, never stored source wording.
- Add a readable recipe view that can become the basis for Kitchen Mode.

Tests:

- Invalid drafts cannot be approved.
- Editing a draft does not mutate an approved recipe implicitly.
- Search and archive filters remain household-scoped.
- Scaling handles absent/non-numeric amounts without fabricating quantities.

### Packet 1C: Milestone integration and UX acceptance

**Owner:** integration agent  
**Depends on:** 1A and 1B

Tasks:

- Connect recipe ingredient mapping to pantry taxonomy and add review states for unresolved mappings.
- Seed a small German-language household dataset for tests and visual review.
- Run accessibility, phone/tablet/desktop layout, performance, backup, and ARM64 smoke checks.
- Verify all empty, loading, validation, conflict, and unavailable-provider states are actionable.

## Milestone 2: Plan, shop, and cook

**Exit gate:** Starting with approved recipes and pantry states, a user can create a week, generate a stable shopping list, purchase items into inventory, cook a planned meal, and observe correct history and planned-stock warnings.

### Packet 2A: Weekly planning

**Owner:** planning agent  
**Depends on:** Milestone 1

Tasks:

- Implement unique household/week plans, slots, serving overrides, notes/leftovers, movement, deletion, and cooked state.
- Support pointer movement plus explicit keyboard/touch move controls; drag-and-drop alone is insufficient.
- Identify recent repeats and duplicate recipes already in the selected week.
- Implement optimistic concurrency for slot updates.

Tests:

- Locale week boundaries, date/slot validation, serving changes, move conflicts, and cooked-slot behavior.
- At most one cook event per slot; undo follows the domain contract.

### Packet 2B: Shopping calculation and list lifecycle

**Owner:** shopping agent  
**Depends on:** 1A, 1B, and the meal-slot query contract from 2A  
**Can run with:** 2A after fixtures/interfaces are agreed

Tasks:

- Implement deterministic expansion and grouping by canonical ingredient while preserving recipe provenance.
- Scale numeric amounts and sum only compatible normalized units; preserve unknown and incompatible quantity components without unit conversion.
- Generate/refresh calculated entries, exclude `in_stock` by default, visibly include `unknown`, and preserve manual/purchased/skipped entries.
- Implement manual items and open/purchased/skipped transitions with versions.
- Purchase a mapped item and update pantry state in one transaction through an application service/domain event.

Tests:

- Regeneration is idempotent and preserves user intent.
- Purchase rollback leaves both shopping and pantry unchanged when either write fails.
- Ingredients with no amount/unit remain useful list labels without false arithmetic.

### Packet 2C: Cooking and planned-stock protection

**Owner:** cooking workflow agent  
**Depends on:** 2A and pantry transition service from 1A  
**Can run with:** 2B

Tasks:

- Build Kitchen Mode with stable progress, optional timers, Wake Lock request/reacquisition, and a clear fallback when unsupported.
- Implement mark-cooked/undo and cook history. Let users explicitly select any recipe ingredients whose inventory status should change; do not infer depletion for all ingredients.
- Implement the two-step planned-stock confirmation for manual transitions away from `in_stock`.
- Bypass that warning only for the transactional `cook_recipe` path tied to a valid meal slot; retain origin and slot in audit history.

Tests:

- Conflict response names affected upcoming meals and requires the latest entity version on confirmation.
- A crafted client cannot use `cook_recipe` origin through the general inventory endpoint.
- Wake Lock loss/reacquisition and timer behavior are covered by browser tests where supported.

### Packet 2D: Vertical workflow acceptance

**Owner:** integration agent  
**Depends on:** 2A through 2C

Run one browser-level scenario from recipe creation to planning, list generation, purchase, cooking, history, and refreshed recommendation inputs. Measure Pi response times with representative household data and fix queries with unbounded scans or N+1 access.

## Milestone 2.5: Catalog enrichment and operations

**Exit gate:** A household owner can manage categories and examples, receive reviewable automatic category assignments, and use generated ingredient icons without blocking the core pantry and meal workflows. The owner can inspect worker, queue, provider, and embedding health; failed enrichment work is safely retryable and provider outages leave manual pantry operation available.

### Packet 2.5A: Category intelligence and review

**Owner:** pantry intelligence agent
**Depends on:** Milestone 2 pantry taxonomy and worker runtime

Tasks:

- Extend household-owned ingredient categories with descriptions, starter examples, owner examples, and configurable similarity thresholds.
- Generate and persist embeddings for categories, examples, and canonical ingredients when enabled; retain local text-similarity fallback when embeddings or Azure are unavailable.
- Queue restart-safe household categorization work, assign only sufficiently confident suggestions, and expose an owner review flow for uncategorized ingredients and alternate choices.
- Keep automatic assignments auditable, allow owners to confirm or correct them, and preserve category history when the starter catalog is synchronized.

Tests:

- Embedding-disabled and provider-failure paths preserve manual category administration and use the documented fallback.
- Confidence, margin, and tie behavior never silently assign an ambiguous category.
- Concurrent/restarted category jobs remain household-scoped and do not duplicate active work.

### Packet 2.5B: Generated ingredient artwork

**Owner:** catalog media agent
**Depends on:** 2.5A category metadata and worker runtime

Tasks:

- Add durable ingredient-icon jobs with queued, running, succeeded, failed, and superseded states.
- Generate recipe-card-compatible ingredient artwork through the worker only, store media outside static assets, and serve the persisted result through the application.
- Keep a clear placeholder and status while generation is pending or unavailable; allow an owner to requeue failed work without blocking pantry changes.
- Bound provider requests by prompt/version and timeout, and redact prompt content, provider payloads, and credentials from logs and diagnostics.

Tests:

- Retried, superseded, and interrupted icon jobs leave one correct final media reference.
- Failed generation retains a usable placeholder and reports a safe actionable status.
- Media access and job actions remain scoped to the owning household.

### Packet 2.5C: Owner operations and diagnostics

**Owner:** operations agent
**Depends on:** 2.5A and 2.5B

Tasks:

- Emit structured, correlated web and worker events with sensitive values redacted.
- Record bounded, sanitized provider diagnostics and expose database, worker heartbeat, queue, recent job, provider, and embedding coverage on an owner-only operations page.
- Provide liveness, readiness, and worker-heartbeat health endpoints; add an embedding connectivity check for deployment troubleshooting.
- Permit explicit retry of failed category and icon jobs while retaining attempt counts and generating a new correlation ID.

Tests:

- Non-owners cannot view operations data or retry another household's work.
- Diagnostics never retain prompts, embeddings, provider payloads, cookies, or credentials and prune to the configured retention bound.
- A stale worker heartbeat and a failed provider request produce actionable, non-sensitive operational states.

## Post-core follow-up: Ingredient substitutions

**Entry gate:** Canonical ingredient normalization and aliases are stable enough
that a household can reliably author directional substitution rules. Promote
this work only after the core plan/shop/cook loop has usage evidence; it is not
part of the Milestone 2 exit gate.

**Detailed handoff:** [Ingredient substitutions: implementation
handoff](ingredient-substitutions.md). An implementation agent must first
verify the handoff against the current codebase and update it for any drift.

**Owner:** planning and shopping agent
**Depends on:** Milestone 2 planning, shopping, and Kitchen Mode; stable
canonical ingredient normalization; versioned slot and shopping mutations.

Tasks:

- Implement household-owned, directional substitution rules and explicit,
  reversible meal-slot ingredient decisions without mutating recipes.
- Add an accessible pre-cooking ingredient-review page from the week plan, and
  render the same effective ingredient list in Kitchen Mode.
- Make accepted substitutions and omissions alter calculated shopping
  contributions for only their affected meal-slot ingredient lines; preserve
  original/effective provenance and existing manual, purchased, and skipped
  list behavior.
- Surface eligible alternatives in calculated shopping items only when the
  action can be tied to a concrete upcoming meal slot and explicitly accepted.
- Derive high-confidence “used before” suggestions solely from prior explicit
  household decisions; post-add prompts remain non-blocking and never apply a
  choice automatically.
- Maintain household isolation, slot/list optimistic concurrency, and
  meaningful stale-version recovery across pre-cooking, shopping, and Kitchen
  Mode.

Cross-feature constraints:

- The effective-ingredient resolver must be shared by planning, shopping,
  Kitchen Mode, and any future recommendation coverage logic; do not duplicate
  resolution rules in templates or views.
- A substitute’s pantry status controls its shopping exclusion; the original
  ingredient is not inferred as unavailable or consumed.
- Cooking inventory changes must validate effective substituted ingredients
  without inferring depletion.
- Omission is a deliberate per-slot decision, not a fake pantry ingredient or
  globally applicable substitution rule.
- Rules, confidence, availability, or prior choices may suggest options but
  never bypass explicit acceptance. Household notes are not allergen, dietary,
  or nutritional advice.

Tests:

- Directional, household-scoped rule authorization and acceptance/revert
  behavior, including cooked, stale, mismatched-line, and foreign-household
  rejections.
- Effective planning and Kitchen Mode lines remain consistent while original
  recipe data remains unchanged.
- Shopping aggregation, stock exclusion, recalculation provenance, omission,
  and retention of manual/purchased/skipped items.
- Learned suggestions and post-add prompts never apply or recalculate a list
  without a new explicit decision.

## Milestone 3: Assisted import

**Exit gate:** URL, supported image, and supported PDF imports execute asynchronously, survive process restarts, produce reviewable drafts or actionable failures, and cannot publish a recipe without user approval. Duplicate source content reuses cached provider output.

### Packet 3A: Durable job runner and import state machine

**Owner:** jobs agent  
**Depends on:** Milestone 1

Tasks:

- Implement database-backed claim/lease/retry semantics using row locking supported by PostgreSQL.
- Add import source storage, content hashes, state/stage transitions, attempt records, cancellation-safe leases, and retry classification.
- Start the worker from the same image with configurable concurrency defaulting to one on the Pi.
- Ensure crashes return expired leases to retry without producing duplicate approved recipes.

Tests:

- Two workers cannot execute one lease concurrently.
- Restart, timeout, retry exhaustion, and idempotency behavior.

### Packet 3B: Safe URL and file acquisition

**Owner:** ingestion security agent  
**Depends on:** 3A

Tasks:

- Implement multipart upload checks for size, extension, MIME type, magic bytes, page count, and private storage.
- Implement HTTPS URL fetching with DNS/IP validation before connection and after every redirect; reject private, loopback, link-local, Tailscale, and cloud metadata destinations.
- Bound redirects, time, decompressed bytes, and content types. Parse structured recipe metadata/readable content without executing page scripts.
- Treat fetched/OCR text as untrusted content throughout the prompt pipeline.

Tests:

- SSRF cases include alternate IP encodings, DNS rebinding strategy, redirects to private ranges, IPv6 local addresses, and oversized/chunked responses.
- Malformed/polyglot files fail before a provider call.

### Packet 3C: Azure extraction and normalization adapters

**Owner:** AI integration agent  
**Depends on:** 3A and fixtures from 3B  
**Can run with:** 3B against stored fixtures

Tasks:

- Implement typed Document Intelligence and Azure OpenAI adapters with timeouts, cancellation, redacted telemetry, and test doubles.
- Keep provider DTOs outside domain entities. Store raw artifacts by reference with retention controls.
- Normalize only extracted recipe content using schema-constrained output and server-side validation.
- Resolve canonical ingredients with confidence/review states; never accept model-supplied database IDs without server verification.
- Cache by content hash, extraction/parser schema version, provider/model deployment, and relevant prompt version.

Tests:

- Golden fixtures cover German recipes, noisy OCR, missing servings, prompt injection text, malformed model output, provider throttling, and partial extraction.

### Packet 3D: Import review and budget controls

**Owner:** import UX agent  
**Depends on:** 3A through 3C

Tasks:

- Build submit, progress polling, failure/retry, review flags, correction, and explicit approval flows.
- Enforce daily job/page/token limits before provider calls and surface safe actionable errors.
- Add independent feature switches for URL import, document import, and LLM normalization.
- Add usage counters and operator metrics sufficient to estimate monthly provider spend.

Pi-first rule: execute orchestration in the local worker by default. Add a Flex Consumption function only if measured imports cause unacceptable Pi contention, provider SDK support is unavailable on ARM64, or reliable execution cannot be achieved locally. The function must accept a job/source reference, be idempotent, authenticate every request, write through the same application contract, and use zero always-ready instances.

## Milestone 4: Recommendations

**Exit gate:** The application ranks approved catalog recipes deterministically with understandable reasons. It remains fully functional when generated ideas are disabled or Azure is unavailable.

### Packet 4A: Deterministic recommendation engine

**Owner:** recommendation agent  
**Depends on:** Milestone 2

Tasks:

- Define and document a versioned score from inventory coverage, unknown/missing ingredients, cook recency, selected preferences, and duplicates in the plan.
- Return component reasons and matched/missing canonical ingredients; use stable tie-breaking.
- Build replayable fixture tests and store a bounded input snapshot/version for debugging.
- Measure query and scoring time on representative Pi data before considering caching.

The baseline score is versioned as `2026-08-1`. It uses 80% pantry coverage (unknown
ingredients count as 40% coverage), adds 10% for a user favorite, and subtracts 10% each
for a recipe cooked in the previous 21 days or duplicated in an upcoming plan. A latest
`dismissed` or `hidden` outcome subtracts 15%. Scores are clamped to `[0, 1]`; ties sort
by case-folded recipe title and recipe ID. Each response stores a bounded, ID-only input
snapshot with the score version for replay/debugging. The candidate query is capped at 100
recipes by default (`RECOMMENDATION_CANDIDATE_LIMIT`); measure it with representative Pi
data before increasing that setting or introducing a cache.

### Packet 4B: Optional generated recipe drafts

**Owner:** AI recommendation agent  
**Depends on:** 3C and 4A

Tasks:

- Generate only when explicitly requested; deterministic catalog recommendations are the default.
- Reuse recipe draft validation and ingredient review. Label source as generated and require approval.
- Apply stricter token/rate budgets and an independent feature switch.
- Ensure unsafe or invalid model output becomes a reviewable failure, never a published recipe.

Generated drafts are disabled by default. Enabling them requires
`RECIPE_GENERATION_ENABLED`, a dedicated Azure deployment, and a per-household rolling
daily limit (`RECIPE_GENERATION_DAILY_LIMIT`). Failed requests retain only a safe error code;
successful output remains a generated draft until its ingredients are mapped and a user
explicitly approves it.

## Milestone 5: Collaboration and recovery

**Exit gate:** Two authenticated household devices see committed inventory and shopping changes promptly, stale writes cannot overwrite current state, reconnects recover through REST, and a documented restore/export exercise succeeds.

### Packet 5A: Real-time gateway and clients

**Owner:** realtime agent  
**Depends on:** Milestone 2 versioned entities

Tasks:

- Authenticate same-origin WebSocket upgrades and assign server-controlled household/list subscriptions.
- Publish minimal events only after transaction commit. Keep HTTP as the only mutation path.
- Apply monotonic versions client-side; refresh through REST on reconnect, gaps, unknown events, or background-tab resume.
- Add heartbeat, connection limits, bounded per-client queues, and slow-client disconnection.

Tests:

- Unauthorized subscriptions are impossible, membership removal closes/revalidates access, rollbacks emit nothing, and reconnect converges to server state.

For one web process, in-memory post-commit fan-out is sufficient. Use a PostgreSQL-backed outbox/notification mechanism only when durability testing shows a meaningful missed-event problem; REST refresh remains authoritative. Do not add Redis for this deployment.

### Packet 5B: Conflict UX and household administration

**Owner:** collaboration UX agent  
**Depends on:** 5A and identity services

Tasks:

- Add owner-managed local membership creation/removal and roles without public registration.
- Present stale-write conflicts with current values and an explicit retry/reapply action.
- Announce meaningful remote changes accessibly using actor display names without relying on color or transient animation.
- Test concurrent purchase, regeneration, pantry status, and membership-removal scenarios.

### Packet 5C: Export, backup, restore, and production acceptance

**Owner:** operations/recovery agent  
**Depends on:** all prior milestones

Tasks:

- Implement authenticated export of approved recipes and stable ingredient references.
- Automate encrypted, paired PostgreSQL/upload backups and retention; keep at least one copy off the Pi.
- Document and execute a clean-host restore, application rollback constraints, secret rotation, and provider-disable procedure.
- Run dependency/security scanning, upload/URL abuse tests, accessibility checks, ARM64 soak testing, and representative performance checks.
- Confirm Azure budgets/alerts, application quotas, and feature switches in the production environment.

## Parallel-agent coordination

Use one integration branch or coordinator and assign packets, not whole milestones, to implementation agents. Before parallel work begins, the coordinator freezes the relevant migration names, entity IDs/version semantics, API examples, and shared test fixtures.

Each agent handoff must contain:

```text
Packet:
Goal and non-goals:
Contract documents and requirement IDs:
Owned files/modules:
Dependencies and agreed interfaces:
Migration/API changes:
Tests to add and commands to run:
Security/privacy considerations:
Pi resource or Azure cost impact:
Evidence returned to coordinator:
```

Agents must not edit another packet's owned module to bypass an interface. They should propose a contract change to the coordinator, update the specification, and then implement against the accepted contract. The integration agent resolves migration ordering, generated client/schema artifacts, shared navigation, and end-to-end fixtures.

## Release gates

Promote a milestone to the Pi only after all of the following pass:

1. Static checks, unit tests, database integration tests, and browser smoke tests.
2. Migration from the previous milestone's database snapshot and restart recovery.
3. ARM64 image build and container health checks under the configured memory limits.
4. Authorization and cross-household isolation tests.
5. Backup of the pre-release database/uploads and a recorded rollback decision.
6. For AI milestones, a disabled-provider test, quota-exhaustion test, cached-retry test, and usage-cost review.

Production deployment should use immutable image tags. Do not automatically apply destructive migrations or enable newly deployed AI features; deploy code, run compatible migrations, verify health, and enable optional provider features separately.