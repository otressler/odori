# Architecture

## System context

```text
Phone / tablet / desktop
        │ HTTPS via Tailscale
        ▼
Traefik reverse proxy ──► Odori web application ──► PostgreSQL
                                  │       │
                                  │       └────────► persistent upload volume
                                  │
                                  ├────────► Azure AI Document Intelligence
                                  └────────► Azure OpenAI
```

The initial architecture is a modular monolith: one web application container owns UI, authenticated HTTP API, domain logic, and background-job execution. PostgreSQL is the system of record. This is intentionally operationally small for a Raspberry Pi while keeping provider-specific AI code behind adapters.

Azure Document Intelligence and Azure OpenAI already perform the expensive OCR and model inference remotely; the Pi only validates, transfers, orchestrates, and persists jobs. Keep orchestration in the local worker initially. An Azure Functions Flex Consumption component is an optional later execution target, not a required service.

## Components

| Component | Responsibility | Boundary |
| --- | --- | --- |
| Web UI | Responsive catalog, inventory, planner, shopping, cooking, import-review screens, and a secure WebSocket client. | Browser; never calls Azure directly. |
| Application API | Authentication, validation, authorization, domain workflows, JSON/HTML responses, and real-time event publishing. | Only component exposed through Traefik. |
| Realtime gateway | Authenticates WebSocket upgrades, authorizes household/resource channels, and sends entity-change events. | Runs in the application container initially. |
| Job runner | Executes URL fetches, document extraction, LLM parsing, and retryable recommendation generation. | Can run as a worker process from the same image. |
| PostgreSQL | Transactional domain records, jobs, import results, and audit metadata. | Internal Docker network only. |
| File storage | Original uploaded PDF/images and optional extracted artifacts. | Named volume initially; backup with database. |
| Azure adapters | Document Intelligence and OpenAI request/response mapping. | Server-side REST calls; inputs minimized and logged safely. |

## Module boundaries

| Module | Owns |
| --- | --- |
| Recipes | Recipe drafts, approved recipes, ingredients, instructions, tags, sources, and imports. |
| Pantry | Canonical ingredients, categories, inventory status, and inventory event history. |
| Planning | Weeks, meal slots, planned recipe servings, and cook history. |
| Shopping | Lists, calculated/manual list entries, purchase state, and inventory synchronization. |
| Recommendations | Feature assembly, scoring, generated recipe drafts, explanations, and provider prompts. |
| Identity | Local household users, sessions, roles, and audit actor identity. |

Modules communicate through application services and transactions, not direct cross-module table updates. For example, Shopping emits a `shopping_item_purchased` domain event; Pantry consumes it to set stock availability.

## Key workflows

### Import pipeline

1. Validate source type, size, MIME type, and URL safety.
2. Persist an `import_job` in `queued` state and store the source reference.
3. A worker fetches a URL or sends the document to Document Intelligence.
4. Store raw response/text separately from the editable recipe draft.
5. Send only relevant extracted recipe text to the LLM with a strict structured-output schema.
6. Resolve ingredient mentions against canonical ingredients; leave low-confidence matches as review-needed.
7. Mark the job `awaiting_review`, `completed`, or `failed`; never auto-publish a recipe.

### Recommendation pipeline

1. Read approved catalog recipes, current inventory statuses, and recent cook history.
2. Compute deterministic coverage first: matched, missing, and unknown ingredient tags.
3. Rank catalog recipes using inventory coverage, recency penalty, preference signals when available, and plan duplication.
4. Optionally ask the LLM for novel recipes using a constrained ingredient set.
5. Return reasons and missing ingredients. Generated recipes remain `draft` until saved/reviewed.

### Shopping calculation

1. Expand every planned recipe's ingredient list according to planned servings.
2. Group entries by canonical ingredient tag and compatible normalized unit; retain original lines and separate incompatible/unknown quantity components as traceability.
3. Exclude `in_stock` items by default. Treat `unknown` and `needs_replenishment` as needed, with a visible status.
4. Upsert calculated entries while preserving manual entries and checked/purchased state.
5. Purchasing an entry changes linked inventory status transactionally.

### Planned-stock warning

1. When inventory is changed manually, Planning finds upcoming meal slots whose approved recipes reference that canonical ingredient.
2. If the item is currently `in_stock` and the requested status is not, the API returns the affected upcoming meals and requires an explicit confirmation.
3. Marking a recipe meal slot cooked may include explicit ingredient status changes selected in the cooking flow. The server verifies that each ingredient belongs to the recipe, applies those changes with origin `cook_recipe`, bypasses confirmation, and retains the meal-slot reference in each audit event. It never assumes all recipe ingredients were depleted.
4. The resulting inventory change and any affected shopping-list updates publish real-time events.

### Real-time collaboration

1. After normal session authentication, a browser opens one same-origin WebSocket connection.
2. It may subscribe only to its household inventory channel and shopping-list channels it is authorized to read.
3. After a successful transaction, the application publishes a minimal versioned event such as `inventory.item.updated`, `shopping.item.updated`, or `shopping.list.regenerated`.
4. Clients apply events only when they advance the local entity version; gaps, reconnects, or unknown events trigger a REST refresh.
5. HTTP remains the mutation authority. WebSockets broadcast confirmed state only and never accept domain mutations.

## Technology decisions

| Concern | Initial choice | Rationale |
| --- | --- | --- |
| Application shape | Server-rendered responsive web app with JSON endpoints | Fast local UX, simple deployment, and one authentication boundary. |
| Database | PostgreSQL | Durable transactions, JSON support for provider artifacts, and reliable backup tooling. |
| Background work | Database-backed job queue and separate worker command | No additional broker on the Pi; jobs survive application restart. |
| Real-time transport | Same-origin WebSockets with database-backed event fan-out when running multiple app processes | Immediate shared-list updates without making socket messages a second write API. |
| File storage | Docker named volume mounted outside the web root | Keeps sources private and simple to back up. |
| AI integration | Azure REST adapters with typed schemas | Limits vendor coupling and makes failures/test doubles manageable. |
| Authentication | App session authentication, with all ingress gated by Tailscale | Tailscale is network access, not sufficient application authorization. |

## Workload placement

| Workload | Initial location | Move only when | Cost/resource rule |
| --- | --- | --- | --- |
| HTTP, server rendering, and WebSockets | Pi web process | No planned move for household scale | One process; keep ordinary use independent of Azure. |
| Domain logic and PostgreSQL transactions | Pi application/database | No planned move | Azure workers must not become a second domain-write implementation. |
| Import job orchestration | Pi worker, concurrency 1 | Measured contention, ARM64 provider incompatibility, or repeated execution-reliability failure | Database-backed jobs remain authoritative. |
| OCR/layout extraction | Azure Document Intelligence | Already remote | Apply page/job quotas and content-hash caching. |
| Recipe normalization/generation | Azure OpenAI | Already remote | Use small approved deployments, strict token limits, and independent feature switches. |
| Optional burst transformation | Flex Consumption function | A benchmark shows meaningful Pi CPU/memory pressure and the data handoff remains simpler than local execution | Zero always-ready instances; idempotent requests; no independent database ownership. |
| Backups | Local encrypted staging plus optional low-cost off-device object storage | Off-device copy is strongly recommended | Retention/lifecycle policy; do not run a general-purpose Azure VM. |

Do not introduce a function merely as a proxy to Document Intelligence or Azure OpenAI: that adds another credential, deployment, and failure boundary without removing the provider call. If a function is introduced, the Pi should submit an idempotency key and bounded input, then poll or receive a safely authenticated result through a documented contract. The function must not require inbound public access to the Pi or direct access to the Pi's PostgreSQL instance.

## Resource and availability model

- Size for a Raspberry Pi 5 with 8 GB RAM and reserve at least 2 GB for the host and shared infrastructure.
- Use one web process, one worker process, and one PostgreSQL instance. Add process concurrency only after measurement.
- Bound database pools, request bodies, result sets, WebSocket queues, upload sizes, job concurrency, provider retries, and log retention.
- AI and URL imports are degradable features. Catalog, pantry, planning, shopping, and cooking remain available during provider or internet outages.
- PostgreSQL and the uploads volume are one recovery unit. Real-time events are disposable notifications; REST and persisted versions restore truth after reconnect.

## AI safety and quality controls

- Treat URL and OCR content as untrusted data, never as instructions.
- Require schema-constrained model output; validate types, maximum lengths, and referenced canonical IDs before persistence.
- Preserve source text and model/version metadata for review and future reprocessing.
- Use confidence thresholds: low-confidence ingredient mappings and missing mandatory fields require human review.
- Apply per-user/job size limits, retry only transient provider errors, and expose provider failure without leaking credentials.
- Cache only deterministic provider outputs keyed by source content hash and parsing schema version.

## Security considerations

- Block URL imports targeting loopback, link-local, RFC1918, Tailscale, and metadata-service address ranges; resolve and re-check redirects to prevent SSRF.
- Permit only HTTPS URL imports initially, cap redirect count, download size, and request time.
- Validate file magic bytes, allow-list image/PDF types, cap upload size/pages, and store uploads outside public paths.
- Keep Azure credentials in environment variables or Docker secrets, never client bundles or the database.
- Use secure, HttpOnly, SameSite session cookies and CSRF protection for mutating browser requests.
- Authenticate WebSocket upgrades using the established session; authorize every channel subscription and close/revalidate sockets when membership changes.
- Do not put secrets, full recipe content, or inventory snapshots into event payloads; send resource ID, version, actor display name where appropriate, and changed fields only.
- Record actor, time, and source for recipe approval, inventory changes, purchases, and import retries.
