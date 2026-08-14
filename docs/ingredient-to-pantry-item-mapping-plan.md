# Ingredient-to-pantry-item mapping implementation plan

> **Verification hint for future agents:** Verify every assumption in this document against the
> current codebase, migrations, templates, routes, settings, and tests before implementation.
> This is a forward-looking plan, not an assertion that all described extension points still exist.

## Verified implementation notes

The first implementation slice confirms that `RecipeIngredient` already has the planned
`match_state`, while candidate scores, methods, and policy/model metadata were previously absent.
The existing matcher also selected either fuzzy or vector score rather than combining compatible
signals, and the runner-up margin was hard-coded. Manual recipe assignment created a canonical
ingredient directly from source text, so it could duplicate an existing pantry ingredient. The new
mapping service addresses these gaps for recipe creation, candidate lookup, and explicit assignment;
durable review records, correction provenance, and free-text shopping capture remain follow-up work.

## Purpose

Provide a reliable way to map ingredient text from recipes, imports, shopping entries, and manual
capture to the household's existing pantry item. The feature should reduce duplicate ingredients,
keep inventory and shopping data connected, and preserve human control when the match is uncertain.

In the current domain, the pantry item is represented by the combination of:

- `CanonicalIngredient`: the household-scoped identity, name, aliases, and optional category.
- `InventoryItem`: the household-scoped availability record for a canonical ingredient.

The canonical ingredient is the semantic matching target. `InventoryItem` is a status record and
should not receive a separate copy of the ingredient identity or embedding unless verification
shows that the domain has changed.

## Current implementation baseline to verify

The existing code already provides important building blocks:

1. `CanonicalIngredient` stores `name`, JSON `aliases`, an embedding, and an embedding-model
   identifier.
2. `pantry.semantic` provides normalized text, fuzzy similarity, Azure embedding calls, cosine
   similarity, ranked ingredient search, and `best_match`.
3. `recipes.services` uses `best_match` when turning structured recipe ingredient lines into
   `RecipeIngredient` records.
4. Low-confidence recipe matches can remain unresolved and can later be assigned through the
   recipe UI.
5. `InventoryItem` has one row per household and canonical ingredient, with a coarse status and
   optimistic version field.
6. Shopping aggregation groups calculated entries by canonical ingredient and purchasing an entry
   updates inventory.
7. Category suggestions are already processed asynchronously through a database-backed job.
8. The inventory page currently searches by canonical ingredient name and displays inventory status.

Before implementation, inspect the actual versions of these modules and confirm whether any
parallel import, recipe, shopping, or inventory work has changed their contracts.

## Goals

- Map equivalent ingredient wording to one household canonical ingredient.
- Use deterministic matching first and vector similarity as an additional signal, not as an
  unconditional authority.
- Auto-accept only high-confidence, sufficiently separated matches.
- Present candidates and explanations for ambiguous matches.
- Create or reuse the corresponding `InventoryItem` only after a canonical ingredient is selected.
- Learn from explicit user corrections without silently changing prior approved mappings.
- Keep core pantry, recipe, planning, and shopping flows usable when embeddings are disabled or
  unavailable.
- Keep all mappings household-scoped and authorization-safe.
- Make model-version changes, retries, and provider failures observable and recoverable.

## Non-goals

- Mapping ingredients to generated images or pantry icons.
- Tracking quantities, units, package sizes, brands, allergens, or nutrition.
- Automatically merging canonical ingredients without a reviewable correction path.
- Replacing the existing category-classification feature.
- Introducing a vector database before measuring whether the existing database-backed approach is
  insufficient.
- Sending browser requests directly to Azure.
- Inferring that an ingredient is in stock merely because a recipe contains it.

## Terminology and invariants

| Term | Meaning |
| --- | --- |
| Source text | Original ingredient wording, such as `2 reife Tomaten` |
| Candidate | A canonical ingredient proposed as a possible match |
| Mapping | A source occurrence linked to a canonical ingredient |
| Pantry item | The household inventory record associated with a canonical ingredient |
| Automatic match | A mapping accepted without a user click because thresholds passed |
| Review-needed | A mapping with candidates but insufficient confidence |
| Unresolved | No safe candidate was found |
| Manual override | A user-selected mapping that automation must not replace |

Required invariants:

1. A canonical ingredient belongs to exactly one household.
2. An inventory item may only reference an ingredient in the same household.
3. A recipe line retains its original source text even after mapping.
4. A low-confidence candidate never changes inventory status.
5. A correction must not rewrite historical shopping or inventory events.
6. An embedding is comparable only with vectors created by the same compatible model/version.
7. Empty, malformed, or provider-failed vectors fall back to text matching.

## Proposed user experience

### Entry points

Support the same mapping service from each entry point rather than implementing independent
matching rules:

- Recipe import review: show a candidate beside each imported ingredient line.
- Recipe edit form: allow a user to assign or change the canonical ingredient.
- Manual pantry capture: search existing ingredients before offering creation.
- Shopping/manual item flow: offer a canonical ingredient when the text resembles one.
- Optional owner/admin review: show a queue of unresolved or low-confidence mappings.

### Candidate presentation

For each unresolved or review-needed line, show:

- Original ingredient wording.
- Proposed canonical ingredient name.
- Match reason: exact alias, normalized text, fuzzy text, semantic/vector, or combined.
- Confidence as plain language and optionally a percentage; do not imply scientific certainty.
- One to three alternatives.
- Actions: accept, choose another, create new canonical ingredient, or leave unresolved.

The UI must not make a user choose between candidates before showing the original wording. On
small screens, use a stacked card or disclosure component; on desktop, use a compact review table.
All actions must be keyboard accessible, labelled, and distinguishable without color alone.

### Manual correction learning

When a user confirms a source phrase for a canonical ingredient:

1. Preserve the source phrase on the owning recipe/import record.
2. Add a normalized alias or mapping example only if it is not already present.
3. Record the source and actor if the existing audit model supports it.
4. Queue an embedding refresh only when the canonical ingredient's searchable text changes.

Do not add every automatically matched phrase as an alias. Automatic matches can reinforce a bad
mapping and make later correction harder.

## Matching design

### Candidate scope

Always scope candidates to the authenticated household and active canonical ingredients. Exclude
inactive ingredients unless displaying the historical mapping or offering an explicit restore
flow. Never search across households.

### Search text

Build a normalized search representation from:

- Original ingredient text.
- Normalized source text.
- Existing canonical name.
- Active aliases.
- Optional structured quantity/unit fields only after verifying their schema and semantics.

Do not embed quantities as if they were ingredient identity. For example, `500 g Tomaten` should
not become a different semantic item from `Tomaten`.

### Ranking stages

Use a staged pipeline to keep common cases fast and explainable:

1. Normalize case, punctuation, whitespace, and German spelling variants using the existing
   normalization helper after verifying its behavior.
2. Check exact canonical names and exact active aliases.
3. Calculate existing fuzzy text scores for names and aliases.
4. If embeddings are enabled and compatible, calculate a query vector and cosine scores against
   canonical ingredient vectors.
5. Combine signals only after defining and testing the weighting. Do not automatically replace the
   current `best_match` behavior without comparing existing test expectations.
6. Sort deterministically by final score and canonical name.
7. Return the top candidate, alternatives, component scores, and the matching method.

### Confidence policy

Define thresholds as settings or a versioned policy rather than magic numbers in views:

- **Automatic:** score meets the auto-match threshold and exceeds the runner-up by the required
  margin.
- **Review-needed:** a candidate exists but the score or margin is below automatic acceptance.
- **Unresolved:** no candidate reaches the review threshold.

Calibrate thresholds with representative German ingredient phrases, plurals, spelling mistakes,
brands, preparations, and compound ingredients. Record false positives and false negatives before
raising the automatic threshold.

### Provider fallback

If embeddings are disabled, misconfigured, timed out, or return malformed data:

- Continue with exact, alias, normalized, and fuzzy matching.
- Mark the method and provider state in diagnostics where existing observability supports it.
- Never block manual recipe editing or pantry capture solely because Azure is unavailable.

## Data-model work

First verify whether existing fields can represent the feature. Prefer extending existing models
over introducing duplicate identity tables.

Likely additions or adjustments:

### Mapping metadata

If the current recipe-line model lacks these concepts, add fields for:

- mapping state: unresolved, suggested, matched, or manually confirmed;
- match method;
- confidence score;
- model/policy version;
- optional candidate artifact for review.

Use the existing `RecipeIngredient` match-state conventions where possible.

### Alias provenance

If `CanonicalIngredient.aliases` is insufficient for safe learning, introduce a separate,
household-scoped alias/example model with:

- canonical ingredient foreign key;
- original text and normalized text;
- source type and source identifier;
- created-by actor;
- active/revoked state;
- timestamps;
- optional embedding and model version.

Do not migrate to a separate model solely for theoretical flexibility; verify query volume and
the need for provenance first.

### Mapping review record

If review must survive a request or import-job retry, add a durable review record rather than
keeping candidate state only in session or template context. It should reference the source
record, household, candidates, scores, policy/model versions, state, and resolution actor.

### Inventory creation

Reuse the existing unique household/ingredient constraint and `get_or_create` behavior. Ensure
that automatic mapping does not create duplicate `InventoryItem` rows and does not overwrite an
existing status. Creating a pantry item should initialize the existing unknown status unless the
user explicitly selected another status.

Every schema change requires:

- migration;
- household-boundary tests;
- rollback or deployment-order notes;
- backfill strategy for existing recipe lines and ingredients;
- indexes justified by measured query patterns.

## Service and API work

Create one domain-level mapping service, likely under the pantry or recipe boundary after verifying
module ownership. It should accept source text and household context and return a structured result,
not an HTML fragment.

The result should include:

- selected candidate, if any;
- ordered alternatives;
- total score and component scores;
- confidence state;
- matching method;
- embedding/provider state;
- model and policy versions;
- whether user confirmation is required.

All callers must pass household context. The service must not accept an arbitrary candidate ID
without checking household ownership and active state.

Add or extend JSON endpoints only after inspecting existing API conventions. Endpoints should
support:

- candidate lookup for a source phrase;
- accepting a suggestion;
- selecting an alternative;
- creating a new canonical ingredient;
- adding a correction/alias;
- resolving a review item.

Use CSRF protection for browser mutations, validate maximum lengths, reject malformed IDs, and
return stable error codes. Keep mutations transactional when mapping and inventory creation are
performed together.

## Background processing

Use the existing database-backed worker pattern where asynchronous work is necessary.

Possible jobs:

1. **Embedding refresh:** populate missing vectors and refresh vectors whose model version is stale.
2. **Bulk remapping preview:** calculate proposed changes without mutating approved mappings.
3. **Review notification/queue maintenance:** identify unresolved import or recipe lines.

Do not automatically remap manually confirmed records. For automatically matched records, require
an explicit migration policy before changing an existing mapping after a model refresh.

Jobs must be idempotent, bounded, retry only transient provider failures, persist error codes, and
remain safe if interrupted. Verify worker dispatch and recovery conventions before adding a new job
type.

## UI implementation outline

1. Add a reusable candidate-review partial or component in the existing server-rendered template
   style.
2. Add clear pending, loading, provider-unavailable, no-match, and success states.
3. Keep the original ingredient line visible beside the canonical pantry item.
4. Make accept/change/create actions progressively enhanced so basic forms work without JavaScript
   where practical.
5. Update inventory and shopping displays only after the server confirms the mapping.
6. Preserve focus after inline candidate actions and announce updates to assistive technologies.
7. Add German UI strings consistent with the existing product language.

## Testing strategy

### Unit tests

- normalization of punctuation, accents, plurals, and German variants;
- exact canonical-name and alias matches;
- fuzzy-only fallback;
- vector similarity with compatible and incompatible model versions;
- combined-score ordering and deterministic tie breaking;
- threshold and runner-up margin behavior;
- empty vectors, malformed vectors, disabled embeddings, timeouts, and provider errors;
- manual override protection;
- alias/example deduplication.

### Service and authorization tests

- candidate queries never cross household boundaries;
- inactive ingredients are excluded from new matches;
- accepting a candidate retains source text;
- creating a new ingredient and inventory item is atomic;
- existing inventory status is not overwritten;
- stale versions are rejected according to current optimistic-locking rules.

### Integration tests

- imported recipe lines are auto-matched when confidence is high;
- ambiguous lines appear in review and do not silently publish;
- accepting a review result flows into shopping aggregation;
- purchase updates the matched inventory item;
- existing recipe editing and manual assignment behavior remains compatible.

### Browser/accessibility checks

- keyboard-only candidate review;
- mobile layout and touch target sizes;
- focus and screen-reader announcements;
- provider failure and no-match messaging;
- no JavaScript path does not lose the original text.

## Rollout and migration

1. Ship read-only candidate calculation and diagnostics first.
2. Compare vector and existing fuzzy results without changing persisted mappings.
3. Review a sample of false positives and false negatives.
4. Enable automatic matching behind a feature switch, initially for new records only.
5. Enable review UI and correction learning.
6. Backfill missing canonical embeddings in bounded batches.
7. Consider remapping existing unresolved lines only through a preview and explicit approval.
8. Monitor auto-match acceptance, correction rate, unresolved rate, provider failures, and latency.
9. Document disabling embeddings without disabling the pantry and recipe workflows.

## Operational and security requirements

- Keep provider credentials server-side and out of logs.
- Bound source-text length before embedding requests.
- Avoid logging full recipe content or household inventory snapshots.
- Rate-limit expensive candidate lookup paths if they invoke the provider.
- Cache deterministic query embeddings using a key that includes deployment/model version and
  normalized text.
- Ensure generated diagnostics contain IDs and scores, not secrets or unnecessary source content.
- Add alerts for job backlogs, repeated provider errors, and unusual automatic-match volume.

## Suggested agent work packets

### Packet A: Codebase verification and decision record

Inspect current models, recipe import, recipe editing, pantry APIs, shopping services, settings,
worker registration, migrations, and tests. Produce a short decision record identifying reusable
contracts, exact files to change, and any conflicts with this plan.

### Packet B: Matching service

Implement or refine the household-scoped ranking service, structured result, thresholds, model
compatibility, fallback behavior, and unit tests. Preserve current `best_match` compatibility until
callers are migrated and tests are updated deliberately.

### Packet C: Persistence and migrations

Add only the metadata required for review, provenance, and safe model refresh. Implement migrations,
constraints, indexes, and backfill/recovery notes.

### Packet D: Review workflow

Add service mutations and authenticated endpoints for accepting, changing, creating, and learning
from mappings. Add authorization, transaction, stale-write, and integration tests.

### Packet E: UI and accessibility

Implement review surfaces in existing template conventions, responsive states, German copy, and
keyboard/accessibility behavior.

### Packet F: Rollout and operations

Add feature switches, bounded embedding refresh jobs, diagnostics, metrics, operator documentation,
and a read-only comparison mode.

## Acceptance criteria

The feature is ready for release when:

- High-confidence ingredient phrases map to the correct household canonical ingredient.
- Ambiguous phrases show alternatives and require confirmation.
- No mapping can cross household boundaries.
- Manual corrections persist and are not overwritten by refresh jobs.
- Inventory and shopping use the selected canonical ingredient without duplicate rows.
- Existing fuzzy-only operation still works with embeddings disabled.
- Provider failures are visible but do not make core editing unavailable.
- Existing recipe import, editing, inventory, and shopping tests pass.
- Accessibility and responsive review flows are verified.
- Operators can backfill, pause, retry, and inspect mapping work safely.
