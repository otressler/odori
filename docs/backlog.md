# Product backlog

## Purpose and status

This backlog captures candidate features, product improvements, and design-risk mitigations beyond the committed milestone contracts. An item in this document is not automatically a requirement. Move it into the product requirements and implementation plan only after its open questions are resolved and it is selected for delivery.

The backlog uses four horizons:

| Horizon | Meaning |
| --- | --- |
| `Now` | Validate or include with milestones 0-2 because it protects the core product. |
| `Next` | Strong candidate immediately after the core plan/shop/cook loop works. |
| `Later` | Useful enhancement that should wait for usage evidence. |
| `Conditional` | Implement only when a named measurement or operational condition is met. |

Priority reflects household value and risk reduction, not implementation novelty:

- `P0`: needed to avoid an invalid or unsafe core product decision.
- `P1`: high-value improvement with a clear user problem.
- `P2`: useful after the main workflow is proven.
- `P3`: optional experiment; remove freely if it adds maintenance burden.

## Backlog overview

| ID | Item | Horizon | Priority | Proposed placement |
| --- | --- | --- | --- | --- |
| BL-001 | Validate the non-AI core loop | Now | P0 | Milestones 1-2 |
| BL-002 | Low-maintenance pantry and stale-state review | Next | P1 | Milestone 2 follow-up |
| BL-003 | Offline-capable shopping PWA | Next | P1 | Before or instead of real-time collaboration |
| BL-004 | Recurring staples and household items | Next | P1 | Shopping follow-up |
| BL-005 | Store-section ordering | Next | P1 | Shopping follow-up |
| BL-006 | Meal-plan templates and rollover | Next | P1 | Planning follow-up |
| BL-007 | Ingredient substitutions | Later | P2 | After normalization is stable |
| BL-008 | Mobile share-target recipe capture | Next | P1 | Assisted import follow-up |
| BL-009 | Recipe annotations and cook feedback | Next | P1 | Before recommendation tuning |
| BL-010 | Guest and occasion serving adjustments | Later | P2 | Planning follow-up |
| BL-011 | Kitchen Mode quality-of-life improvements | Later | P2 | Kitchen Mode follow-up |
| BL-012 | Import resilience and paste fallback | Now | P0 | Milestone 3 |
| BL-013 | Learned normalization corrections | Next | P1 | Milestone 3 follow-up |
| BL-014 | Recommendation usefulness and measurement | Now | P0 | Milestone 4 entry gate |
| BL-015 | Authentication strategy decision | Now | P0 | Milestone 0 ADR |
| BL-016 | Pi storage and disaster-recovery hardening | Now | P0 | Milestones 0 and 5 |
| BL-017 | Collaboration transport decision | Conditional | P1 | Before Milestone 5A |
| BL-018 | Azure offload decision gate | Conditional | P2 | After measured import load |
| BL-019 | Cloud-spend circuit breaker | Now | P0 | Before enabling AI in production |
| BL-020 | Scope and continuation gates | Now | P0 | End of every milestone |

## Core product and pantry

### BL-001: Validate the non-AI core loop

**Problem:** AI import can make data entry faster, but it cannot rescue a planning and shopping workflow that users do not find useful. Building cloud features first would obscure this risk.

**Outcome:** At least one household can use manual recipes to plan, shop, and record cooking for several weeks, and the observed friction determines the next milestone.

**Dependencies:** Implementation Milestones 1 and 2. No Azure configuration.

**Implementation plan:**

1. Seed or manually enter 10-20 representative recipes, including missing amounts, optional ingredients, incompatible units, and pantry staples.
2. Run at least three complete weekly cycles: plan meals, generate a list, modify it, shop, mark selected pantry changes, and record cooked meals.
3. Add privacy-respecting product events for workflow completion and correction counts. Store aggregates locally; do not record recipe text, ingredient names, or shopping labels in telemetry.
4. Record abandonment points and manual corrections through a short in-app feedback prompt or a structured test log.
5. Review the continuation metrics at the Milestone 2 gate before enabling assisted imports.

**Acceptance criteria:**

- The full loop works with Azure disabled and no developer/database intervention.
- The team can report plan completion, list regeneration, manual list corrections, pantry corrections, and cooked-meal completion without exposing household content.
- The review produces a ranked list of observed problems and an explicit go/change/stop decision for Milestone 3.

**Pi/cost impact:** Negligible. Local event rows must have bounded retention or aggregation.

**Open questions:**

- Who will participate in the first household trial, and what duration is sufficient: three weeks or a fixed number of shopping trips?
- What result indicates success: repeated weekly use, fewer forgotten items, reduced planning time, or subjective usefulness?
- Should local product analytics be retained at all, or is a manual test journal sufficient for a personal project?
- Which corrections are worth instrumenting without collecting sensitive household content?

### BL-002: Low-maintenance pantry and stale-state review

**Problem:** `available` can remain true after unrecorded consumption, while demanding constant pantry maintenance would cause users to stop updating it.

**Outcome:** Pantry state is quick to confirm, visibly ages, and remains advisory rather than presenting false certainty.

**Dependencies:** Pantry domain and inventory history from Packet 1A.

**Implementation plan:**

1. Add `last_confirmed_at` and confirmation actor/source to inventory items or derive them efficiently from inventory events.
2. Add a household-configurable staleness threshold, initially one global default rather than per-category rules.
3. Build a “check the kitchen” flow ordered by store/pantry category, showing stale and unknown items first.
4. Provide bulk category confirmation with a reviewable summary and one audit event per changed item.
5. Add an `always_check` shopping policy for ingredients that should not be automatically excluded merely because they are `available`.
6. Create inventory items lazily when a recipe mapping, shopping purchase, or explicit pantry action first needs them.
7. Seed sensible categories and allow a household to mark common non-purchased ingredients such as water as ignored for shopping.
8. Do not silently mutate stale items to `unknown`; show age and let the user confirm or change them.

**Acceptance criteria:**

- A user can review a representative pantry on a phone without opening every item.
- Old `available` values are visibly distinguishable from recently confirmed values.
- `always_check` items remain on calculated shopping lists with an explanation.
- Bulk confirmation is auditable and can recover cleanly from a stale version conflict.
- Recipe creation does not require up-front pantry setup.

**Pi/cost impact:** Local-only. Index the stale-item query and avoid generating periodic background writes merely to mark age.

**Open questions:**

- What is the default stale threshold: 14, 30, or 60 days?
- Is one household-wide threshold adequate, or do fresh and shelf-stable categories need different defaults?
- Should `always_check` be an inventory policy, a canonical-ingredient policy, or a per-list choice?
- Should ignored ingredients be excluded from both shopping and pantry UI, or only from shopping calculation?
- Does bulk “all in stock” create too much false confidence to be offered?

## Shopping and planning

### BL-003: Offline-capable shopping PWA

**Problem:** Tailscale or mobile connectivity can be unavailable or unstable inside a store. The active shopping list is most valuable precisely in that environment.

**Outcome:** The current list opens and remains usable offline; queued item changes converge safely after connectivity returns.

**Dependencies:** Versioned shopping APIs from Packet 2B. The authentication/session strategy must define offline expiry behavior. Coordinate with BL-017.

**Implementation plan:**

1. Add a web app manifest, installable icons, and a narrowly scoped service worker.
2. Cache only the application shell and the latest explicitly opened active list; never cache provider artifacts, recipe uploads, authentication responses, or arbitrary API traffic.
3. Store an encrypted-at-rest guarantee only if the browser platform can actually provide it; otherwise document that browser storage contains the active list on the device.
4. Queue state transitions with operation IDs, base entity versions, timestamps, and the authenticated user/household context.
5. Make purchase operations idempotent server-side. Replay queued operations in order when the same authenticated session reconnects.
6. On version conflict, fetch current state and present explicit keep-server/reapply choices. Never silently overwrite a remote purchase or regeneration.
7. Provide clear offline, syncing, conflict, and session-expired states. Do not claim synchronization before server acknowledgement.
8. Test install/update behavior and offline shopping on supported Android/iOS browsers; include service-worker upgrade and cache invalidation tests.

**Acceptance criteria:**

- An active list opened online can be reopened and checked while the server is unreachable.
- Repeated replay of the same queued purchase does not duplicate inventory events.
- A remote list regeneration or purchase conflict is visible and recoverable.
- Signing out or removing local offline data clears cached household list content.
- Non-shopping pages fail safely rather than serving stale sensitive data as current.

**Pi/cost impact:** No Azure cost. Browser and API complexity is moderate; server storage impact is limited to idempotency records with bounded retention.

**Open questions:**

- Which browsers/devices are mandatory, especially iOS Safari where PWA behavior differs?
- May an installed device retain the active list after the server session expires?
- How long should idempotency keys and queued operations remain valid?
- Should offline mode support manual-item creation, or only state changes to already cached items initially?
- Is device-level exposure of shopping-list content acceptable, or is a user-facing “offline storage” opt-in required?

### BL-004: Recurring staples and household items

**Problem:** A useful shopping list includes non-recipe necessities and frequently replenished staples. Re-entering them manually each week adds friction.

**Outcome:** Users can maintain a small reusable set of household items and add due items to the active list without confusing them with recipe-calculated ingredients.

**Dependencies:** Manual shopping items from Packet 2B.

**Implementation plan:**

1. Add a `recurring_item` owned by the household with label, optional canonical ingredient, cadence/mode, default section, active flag, and last-added timestamp.
2. Start with two modes: manual “usually buy” and simple interval recurrence. Avoid predictive purchasing.
3. Show due staples during list creation/regeneration and require one-tap confirmation before adding them.
4. Copy recurring items into ordinary manual shopping items so a historical list does not change when the template changes.
5. Prevent duplicate open entries by offering merge/keep-separate choices when a recurring item maps to an existing calculated ingredient.
6. Support pause/archive and preserve list history.

**Acceptance criteria:**

- A recurring item can be added, skipped for one cycle, paused, and archived.
- Regeneration does not repeatedly duplicate the same due item.
- Recipe provenance and recurring origin remain distinguishable.
- Non-food household items do not require fake canonical ingredients or pantry states.

**Pi/cost impact:** Local-only and negligible.

**Open questions:**

- Are interval schedules necessary, or is a reusable checklist enough?
- Should purchasing reset the recurrence date or should adding to the list reset it?
- Can one recurring item map to pantry inventory, or should household goods always remain unmapped?
- When a staple and calculated ingredient collide, should merging be automatic for high-confidence canonical matches?

### BL-005: Store-section ordering

**Problem:** Alphabetical or recipe-based shopping lists cause unnecessary movement through a store.

**Outcome:** A household can order list items according to the sections of its usual store without building multi-store optimization.

**Dependencies:** Ingredient categories and shopping lists. BL-004 should reuse the same sections.

**Implementation plan:**

1. Add household-defined `store_section` records with name, sort order, and optional default category mappings.
2. Store an optional default section on canonical ingredients and recurring/manual templates.
3. Group active-list items by section and allow an item-level override without changing the canonical default unless explicitly requested.
4. Provide simple reorder controls that work with keyboard and touch; do not require drag-and-drop.
5. Include an “Unassigned” section and make assignment possible directly from the shopping list.
6. Defer multiple store profiles, prices, aisle numbers, and route optimization.

**Acceptance criteria:**

- Users can create/reorder sections and see the active list follow that order.
- Unknown/manual items remain usable in “Unassigned.”
- Changing a default does not rewrite historical completed lists.
- Section assignment does not alter recipe ingredient categories.

**Pi/cost impact:** Local-only and negligible.

**Open questions:**

- Is one section order per household enough for the initial version?
- Should section defaults live on canonical ingredients, ingredient categories, or both with a precedence rule?
- Do users need ad hoc list grouping when shopping at a different store?

### BL-006: Meal-plan templates and rollover

**Problem:** Weekly planning repeats patterns, and recreating a useful week from scratch is unnecessary work.

**Outcome:** Users can copy or template plans while preserving explicit control over dates, servings, and leftovers.

**Dependencies:** Meal plans and slot entry types from Packet 2A.

**Implementation plan:**

1. Implement “copy previous week” as the first vertical slice using a preview of target slots and conflicts.
2. Add named templates only after copy-week usage confirms demand. Store relative weekday/slot entries, recipe references, serving defaults, and note/leftover text.
3. Support copying selected slots rather than requiring an entire week.
4. Offer rollover of uncooked future/past entries into available target slots; never move them automatically.
5. Detect archived/deleted recipes and present unresolved entries during preview.
6. Recalculate shopping lists only through an explicit refresh after copied plans are confirmed.

**Acceptance criteria:**

- Copying is previewable, cancellable, and does not overwrite occupied slots without confirmation.
- Templates do not share mutable meal-slot records with live plans.
- Archived recipe references are handled without data loss or broken pages.
- Touch and keyboard users can resolve target conflicts.

**Pi/cost impact:** Local-only. Bound template count only if real usage warrants it.

**Open questions:**

- Is “copy last week” sufficient, making named templates unnecessary?
- Should copied slots retain original serving overrides and notes by default?
- How should leftovers roll over when their source cooked meal is not copied?
- Should shopping-list refresh be offered immediately after copy or remain a separate action?

### BL-007: Ingredient substitutions

**Problem:** Recommendations and shopping decisions can be improved by known household substitutions, but automatic replacement can alter recipe intent or create unsafe assumptions.

**Outcome:** The app suggests explicit household-approved alternatives without silently rewriting recipes, pantry state, or shopping items.

**Dependencies:** Stable canonical ingredient normalization and aliases. Implement after BL-013 behavior is understood.

**Implementation plan:**

1. Add directional household substitution rules between canonical ingredients with an optional note and suitability scope.
2. Begin with user-authored rules; do not generate or globally seed dietary/medical substitutions.
3. Display substitutions in recipe detail, recommendation missing-ingredient reasons, and shopping-item actions.
4. Require an explicit choice to replace a shopping component or mark a recipe as covered by a substitute.
5. Keep the original recipe ingredient and provenance intact. Record the selected substitution on the plan/list context, not as a recipe mutation.
6. Prevent transitive inference and cycles from being applied automatically.

**Acceptance criteria:**

- Directional rules behave directionally and cycles cannot cause infinite resolution.
- Suggestions never remove an original shopping need without explicit confirmation.
- The UI identifies that substitutions are household notes, not safety or allergen advice.
- Deactivating a rule does not alter historical plans/lists.

**Pi/cost impact:** Local-only. No AI call is required.

**Open questions:**

- Are substitutions ingredient-wide or recipe-specific?
- Is a free-text note enough, or are ratios/amount adjustments needed later?
- Should recommendation scoring treat a substitute as fully matched or as a weaker partial match?
- Are dietary restrictions explicitly out of scope even when users author substitution rules?

## Recipe capture and curation

### BL-008: Mobile share-target recipe capture

**Problem:** Copying a URL, opening Odori, navigating to import, and pasting creates avoidable friction on phones.

**Outcome:** An installed Odori PWA can receive a shared recipe URL and open a prefilled, user-confirmed import flow.

**Dependencies:** PWA manifest baseline from BL-003 and URL import from Milestone 3.

**Implementation plan:**

1. Register a narrow Web Share Target accepting URLs and text; do not accept arbitrary files until browser behavior and upload validation are proven.
2. Parse the shared payload into a confirmation screen rather than starting a billable import immediately.
3. Require an authenticated session and preserve the share intent safely through sign-in without putting source content in logs or query analytics.
4. Reuse URL validation, quotas, duplicate-content checks, and import job creation.
5. Provide copy/paste fallback instructions through normal form affordances where share targets are unsupported.

**Acceptance criteria:**

- Sharing a valid HTTPS URL opens a confirmation screen and creates no provider charge until confirmed.
- Unsupported text or multiple URLs produce an editable form, not a failed background job.
- Unauthenticated sharing resumes safely after sign-in or discards the payload explicitly.
- Duplicate submissions reuse or point to existing import work according to the import contract.

**Pi/cost impact:** No additional provider cost beyond confirmed imports.

**Open questions:**

- Which mobile browsers must support the share-target flow?
- Should shared plain text prefill the paste-import fallback in BL-012?
- How long may an unauthenticated share payload be retained, and where?

### BL-009: Recipe annotations and cook feedback

**Problem:** Household adaptations and “next time” observations are valuable but should not overwrite imported source truth. Recommendations also lack a direct preference signal.

**Outcome:** Users can record household recipe notes and per-cook feedback while retaining the original source and approved recipe structure.

**Dependencies:** Recipe lifecycle and cook events. Recommendation work should consume this only after enough data exists.

**Implementation plan:**

1. Separate source attribution/raw import, approved structured recipe, and household annotations in the model and UI.
2. Add recipe-level notes for durable adaptations and cook-event notes for one occurrence.
3. Add a deliberately small rating signal, such as `favorite`, `good`, and `not_again`, rather than a false-precision five-star score.
4. Add optional “next time” notes after marking a meal cooked; keep completion fast and allow skipping.
5. Define whether notes are household-shared or author-private before implementation.
6. Feed only explicit, documented signals into deterministic recommendation scoring and preserve scoring-version reproducibility.

**Acceptance criteria:**

- Editing annotations never changes source text or import artifacts.
- Users can see whether a note applies to the recipe generally or one cook event.
- Recommendation reasons expose when explicit feedback affected ranking.
- Archiving a recipe preserves its historical cook notes and feedback.

**Pi/cost impact:** Local-only; text lengths and history queries must be bounded.

**Open questions:**

- Are annotations shared with the whole household or attributable/private per user?
- Which feedback vocabulary feels natural in German?
- Should `not_again` exclude a recipe or merely rank it lower?
- Does editing the approved recipe create revisions, or is source/annotation separation sufficient initially?

### BL-010: Guest and occasion serving adjustments

**Problem:** Household size varies for guests and events, but full guest management would be excessive.

**Outcome:** A planned meal can record an occasion and serving count without changing the household default or introducing guest identities.

**Dependencies:** Planned serving overrides and shopping scaling.

**Implementation plan:**

1. Add optional occasion text and attendee/serving count to a recipe meal slot.
2. Make serving count the only value used by shopping scaling; attendee count, if distinct, is descriptive.
3. Offer a quick “household + guests” serving control using the household default.
4. Display occasion context in plan and cooking views, not as a separate calendar subsystem.
5. Exclude invitations, contact records, RSVP, dietary profiles, and event sharing.

**Acceptance criteria:**

- Adjusting one occasion does not mutate recipe default servings or other slots.
- Shopping quantities clearly show the slot's serving basis.
- Removing occasion metadata leaves the serving override intact unless explicitly reset.

**Pi/cost impact:** Negligible and local-only.

**Open questions:**

- Is occasion text enough, or is a numeric guest count genuinely useful?
- Should the household have a default serving count separate from membership count?
- Do users need one-off non-recipe shopping items tied to an occasion?

### BL-011: Kitchen Mode quality-of-life improvements

**Problem:** The baseline cooking view supports steps and timers, but ingredient context and multiple concurrent tasks may still require leaving the flow.

**Outcome:** Kitchen Mode reduces screen navigation while keeping timers and hands-free controls honest about browser limitations.

**Dependencies:** Packet 2C and representative cooking feedback from BL-001.

**Implementation plan:**

1. Allow steps to reference recipe ingredient lines without duplicating ingredient data.
2. Show relevant ingredients alongside the current step and retain an accessible full-ingredient view.
3. Support multiple named timers persisted in browser state with server timestamps only if cross-device continuation is required.
4. Reconcile elapsed time after background suspension; clearly state that browser timers are convenience reminders, not guaranteed alarms.
5. Add printable and distraction-free layouts before experimental voice controls.
6. Prototype hands-free next/previous using supported browser APIs only after a privacy and compatibility review; always retain touch/keyboard controls.

**Acceptance criteria:**

- Step references survive recipe scaling and ingredient reorder/edit operations.
- Multiple timers cannot resize or overlap the cooking controls on phone/tablet layouts.
- Background/resume behavior is tested and missed alarms are represented honestly.
- Printing does not include controls, private operational metadata, or clipped instructions.

**Pi/cost impact:** Mostly client-side. Avoid continuous server timer polling or cloud speech services.

**Open questions:**

- Should step-to-ingredient links be authored manually, proposed during import, or both?
- Must timers continue across devices, or is per-device state sufficient?
- Is voice control worth microphone permission and browser inconsistency?
- Which browsers reliably support Wake Lock and notification behavior on the target devices?

## Import and recommendations

### BL-012: Import resilience and paste fallback

**Problem:** URL import is inherently brittle because sites block automation, render client-side, omit structured metadata, or change markup.

**Outcome:** Imports use a predictable fallback ladder and always offer a manual path that preserves user effort.

**Dependencies:** Milestone 3 jobs and ingestion security.

**Implementation plan:**

1. Parse allow-listed Schema.org `Recipe` JSON-LD first, including multiple graph nodes and common field representations.
2. Fall back to bounded readable HTML extraction without executing remote JavaScript.
3. Return an actionable partial/failure state rather than automatically sending arbitrary full pages to an LLM.
4. Add “paste recipe text” as a first-class import source that enters the same normalization and review pipeline.
5. Let users switch a failed URL job to pasted text while retaining source attribution and failure history.
6. Maintain provider/site fixtures and classify failures as blocked, unsupported, unsafe, malformed, transient, or quota-limited.
7. Avoid site-specific scrapers until repeated household usage justifies their maintenance.

**Acceptance criteria:**

- Valid JSON-LD imports without Document Intelligence and sends only bounded recipe content to normalization.
- A blocked or script-only site leads directly to an editable paste fallback.
- Remote scripts, images, and embedded instructions are never executed by extraction.
- Failure categories are safe, actionable, retry-aware, and covered by fixtures.

**Pi/cost impact:** JSON-LD/readability parsing is local and bounded. Paste fallback may use OpenAI normalization under the same quotas and cache rules.

**Open questions:**

- Should pasted text be normalizable without Azure by offering a fully manual draft form?
- Which Schema.org variations are in the first supported fixture set?
- Is source attribution retained when users paste copyrighted recipe text, and what export behavior is appropriate?
- What maximum HTML and pasted-text sizes balance usefulness and abuse resistance?

### BL-013: Learned normalization corrections

**Problem:** German inflection, preparation forms, brands, and near-duplicates will repeatedly create mapping work if user corrections are discarded.

**Outcome:** Confirmed household corrections improve later imports deterministically before another model call is considered.

**Dependencies:** Canonical ingredient aliases, import review, and merge/deactivation rules.

**Implementation plan:**

1. Normalize comparison text conservatively for case and whitespace while retaining original wording.
2. When a user confirms a mapping, offer to save the source phrase as a household alias; do not do so silently for low-confidence or ambiguous phrases.
3. Resolve exact household aliases before model-assisted mapping on subsequent imports.
4. Detect alias collisions and require resolution rather than choosing by frequency.
5. Add a merge preview showing affected recipes, inventory, lists, and aliases. Make merge transactional and retain a redirect/tombstone for old IDs.
6. Record normalization schema versions so behavior changes can be tested and cached results invalidated intentionally.

**Acceptance criteria:**

- A saved alias maps the same phrase without a billable model call.
- Alias collisions never silently remap existing recipes.
- Ingredient merge preserves all household references and audit history.
- Original recipe ingredient text remains unchanged.

**Pi/cost impact:** Reduces Azure usage. Alias lookup must be indexed and household-scoped.

**Open questions:**

- Which text normalization is safe for German without collapsing genuinely different ingredients?
- Should aliases include preparation state such as “gehackte Tomaten,” or should preparation remain recipe wording only?
- Must merges be reversible, or is a preview plus retained redirect sufficient?
- Can owner and member roles both create aliases/merge ingredients?

### BL-014: Recommendation usefulness and measurement

**Problem:** Generated recipes may be expensive and less trustworthy than ranking recipes the household already likes. A scoring system can also appear arbitrary without feedback and measurement.

**Outcome:** Deterministic catalog ranking proves useful before generated ideas receive product investment or budget.

**Dependencies:** Packet 4A, BL-001 usage evidence, and optionally BL-009 feedback.

**Implementation plan:**

1. Define a versioned baseline score with deliberately few factors: pantry coverage, missing/unknown count, recency, explicit feedback, and current-plan duplication.
2. Create fixture households and expected relative rankings rather than brittle exact floating-point scores.
3. Expose human-readable reasons and a “not useful” action with a small reason vocabulary.
4. Record local aggregate outcomes: suggestion opened, planned, cooked, dismissed, or hidden. Do not store a second copy of ingredient snapshots unnecessarily.
5. Tune weights only at explicit scoring-version changes and retain replay fixtures.
6. Gate generated recipes behind explicit demand, a separate quota, and evidence that catalog coverage is insufficient.

**Acceptance criteria:**

- The same version and input produce stable ordering and reasons.
- Users can identify why a recipe is high or low without seeing an opaque model explanation.
- Recommendations remain available with all Azure features disabled.
- A review after representative use decides whether generated recipes proceed, change, or are removed from scope.

**Pi/cost impact:** Local queries/scoring only for the baseline. Bound candidate set and eliminate N+1 queries. Generated recipes retain the strict Azure budget.

**Open questions:**

- What is the primary success signal: planned, cooked, or positively rated?
- How strong should recency and `not_again` penalties be?
- Should unknown pantry items count as missing, partial coverage, or a separate reason only?
- How many approved recipes constitute enough catalog coverage before recommendations are meaningful?
- Is generated-recipe functionality still needed after several weeks of catalog ranking?

## Platform, security, and operations

### BL-015: Authentication strategy decision

**Problem:** Local accounts require bootstrap, password recovery, and session operations, while trusting proxy identity headers can create an authentication bypass if ingress is misconfigured.

**Outcome:** One documented identity strategy fits the household threat model and has a tested recovery procedure.

**Dependencies:** Must be resolved in Packet 0A/0B before feature implementation.

**Implementation plan:**

1. Write an ADR comparing local credentials with verified Tailscale identity integration for the actual Traefik/Tailscale topology.
2. Threat-model direct container access, forged headers, public-interface exposure, device loss, household-member removal, and owner lockout.
3. If local credentials win, implement owner bootstrap, password change/reset from Pi console, session revocation, and no public registration.
4. If Tailscale identity wins, accept identity only from a cryptographically or network-verified trusted proxy path; strip inbound identity headers and prevent bypass around that proxy.
5. Retain application-level user and household records regardless of sign-in mechanism for authorization and audit identity.
6. Add a documented owner recovery and provider-independent sign-in test to release acceptance.

**Acceptance criteria:**

- The ADR names the trust boundary and rejected alternative.
- A request cannot forge another tailnet/local identity through client-supplied headers or direct service access.
- Removing a member prevents new access and invalidates/revalidates active sessions/sockets.
- Owner recovery works without editing application database rows manually.

**Pi/cost impact:** No required Azure cost. Avoid introducing a paid identity provider for a handful of users.

**Open questions:**

- Is each household user guaranteed to have a distinct Tailscale identity?
- Does the deployed Tailscale/Traefik setup expose a verifiable identity signal, or only network membership?
- Is password recovery from local Pi console acceptable?
- Must the app work on shared household devices without distinct user sign-in?

### BL-016: Pi storage and disaster-recovery hardening

**Problem:** The Pi and its storage are a more likely failure point than application scale. Database and uploads must be restored as one coherent set.

**Outcome:** Production runs on suitable durable storage and can be restored to a clean host from an encrypted off-device backup.

**Dependencies:** Deployment Packet 0C and recovery Packet 5C.

**Implementation plan:**

1. Require a reputable SSD or similarly durable storage for PostgreSQL and uploads; keep the boot medium out of the high-write data path where practical.
2. Choose one backup mechanism that captures a PostgreSQL-consistent dump/snapshot and matching uploads manifest with checksums.
3. Encrypt before off-device transfer and define key recovery separately from the Pi.
4. Apply retention and lifecycle rules sized for a personal project; monitor failed backups and destination capacity.
5. Automate a restore verification into an isolated clean environment and run a manual full-host exercise at least quarterly.
6. Record recovery point and recovery time achieved, not merely backup job success.

**Acceptance criteria:**

- A documented clean-host restore recovers approved recipes, source references/uploads, households, plans, and current lists.
- Missing/corrupt upload files are detected by checksums and reported.
- Backup encryption keys are recoverable after total Pi loss but are not stored only alongside backups.
- Restore tests do not overwrite production or send provider requests.

**Pi/cost impact:** Local I/O should run outside normal use. Optional object storage target remains under the documented USD 2/month goal.

**Open questions:**

- Which off-device target is preferred: another household device, Azure Blob, or both?
- What recovery point is acceptable: 24 hours, one week, or another interval?
- Must raw import artifacts be retained, or can approved recipes outlive their original uploads according to policy?
- Who holds and tests recovery of the encryption key?

### BL-017: Collaboration transport decision

**Problem:** WebSockets add authorization, ordering, reconnect, and operational complexity. A handful of users may be satisfied by refresh-on-focus or bounded polling.

**Outcome:** Real-time transport is selected from observed concurrent shopping behavior, while optimistic concurrency remains mandatory in every option.

**Trigger:** Decide after the versioned shopping workflow and preferably BL-003 are in use. Do not implement WebSockets solely because they appear in the architecture.

**Implementation plan:**

1. Instrument or manually observe concurrent-list sessions, stale conflicts, duplicate purchases, and acceptable update delay.
2. Prototype refresh-on-focus plus conditional polling while a shared list is visible. Use ETags/version endpoints to avoid full payload transfer when unchanged.
3. Compare it with the specified same-origin WebSocket gateway using Pi CPU/memory, reconnect behavior, implementation effort, and user-visible delay.
4. Choose the simplest option meeting the measured need and record it in an ADR.
5. Preserve REST as mutation authority and version-gap recovery even if WebSockets win.

**Acceptance criteria:**

- The decision records measured concurrency/update-latency needs and operational cost.
- No transport allows a stale mutation to overwrite current state.
- Background/resume and temporary network loss converge to authoritative REST state.
- Polling, if selected, stops when the view is hidden and uses bounded intervals/backoff.

**Pi/cost impact:** No Azure cost. Polling increases local requests; WebSockets increase persistent connection and code complexity. Either is small at household scale but must be measured.

**Open questions:**

- What update latency is acceptable while two people shop: immediate, 5 seconds, or 30 seconds?
- How often will concurrent shopping actually occur?
- Does offline replay from BL-003 solve more practical conflicts than live events?
- If polling is sufficient, should Milestone 5A be replaced rather than deferred?

### BL-018: Azure offload decision gate

**Problem:** Azure Functions can add authentication, deployment, and failure surfaces without reducing meaningful Pi work because OCR and model inference are already remote.

**Outcome:** Cloud orchestration is introduced only when a benchmark demonstrates a concrete benefit and the monthly budget remains safe.

**Trigger:** Representative imports exceed a defined Pi contention, compatibility, or reliability threshold after local worker tuning.

**Implementation plan:**

1. Benchmark PDF/image/URL jobs on the Pi with worker concurrency one, recording web latency, worker RSS/CPU, job duration, retries, and provider wait time.
2. First reduce local contention through bounded parsing, streaming uploads, process limits, and scheduling.
3. If a problem remains, prototype one idempotent Flex Consumption function for the problematic transformation only.
4. Keep domain writes behind an authenticated application contract or result handoff; do not expose Pi PostgreSQL or open inbound public callbacks.
5. Compare end-to-end reliability, latency, security surface, deployment effort, and estimated monthly cost.
6. Record a go/no-go ADR. Configure zero always-ready instances and a kill switch if adopted.

**Acceptance criteria:**

- The benchmark and threshold that justified offload are reproducible.
- Duplicate function execution cannot duplicate jobs, recipes, or billable downstream calls where caching applies.
- The Pi remains fully usable when the function is unavailable.
- The projected normal Azure total stays below USD 15/month and optional AI can be disabled before USD 50.

**Pi/cost impact:** The item exists to protect both. A function that only proxies Document Intelligence/OpenAI should fail the decision gate.

**Open questions:**

- Which measured condition triggers evaluation: web p95 latency, worker RSS, job timeout rate, or ARM64 incompatibility?
- Is asynchronous polling from the Pi sufficient, or is any callback mechanism necessary?
- Who owns deployment/credential rotation for the optional function?
- Does the extra Azure resource meaningfully improve recovery, or only move orchestration?

### BL-019: Cloud-spend circuit breaker

**Problem:** Azure budget alerts notify but do not cap spending. Large inputs or retry defects can consume the monthly target before an operator reacts.

**Outcome:** Application-side admission control prevents new optional provider work when daily/monthly usage limits are reached.

**Dependencies:** Import job admission and provider usage metadata. Required before production AI switches are enabled.

**Implementation plan:**

1. Define conservative per-household daily limits for document pages, normalization jobs, generated recipes, input characters/tokens, output tokens, and retries.
2. Track reserved and consumed units atomically when a job is admitted/completed so concurrent submissions cannot bypass limits.
3. Separate extraction, normalization, and generation switches and quotas; generated recipes receive the smallest allowance.
4. Reconcile provider-reported usage when available and expose local monthly estimates to the operator.
5. Add a manual circuit breaker and automatic soft/hard thresholds. A hard threshold rejects new billable work while preserving cached and non-AI operations.
6. Test retry storms, provider timeouts, concurrent admissions, clock/month boundaries, and cache hits.

**Acceptance criteria:**

- Reaching a limit produces an actionable non-retry-looping response and no provider request.
- Cache hits do not consume billable quota unless the provider is actually called.
- Optional provider disablement leaves recipe, pantry, planning, shopping, and cooking flows operational.
- Operators can identify which provider/work type consumed the estimate without seeing recipe content.

**Pi/cost impact:** Small local accounting tables. This is required to defend the USD 50 ceiling; Azure alerts remain a second line of defense.

**Open questions:**

- What launch quotas fit expected household use and current regional/model pricing?
- Should an owner be able to override a hard application limit for one job?
- How conservative should estimates be when Azure usage metadata is delayed or absent?
- Is monthly reset based on UTC, household locale, or Azure billing period?

### BL-020: Scope and continuation gates

**Problem:** The combined import, recommendation, collaboration, offline, kitchen, and operations scope is large for a personal project. Optional complexity can delay the useful core indefinitely.

**Outcome:** Every milestone ends with an explicit decision to continue, change, defer, or remove later work based on observed value and maintenance cost.

**Dependencies:** The release gates in the implementation plan and BL-001 measurements.

**Implementation plan:**

1. Add a short decision record at each milestone exit covering household value, unresolved defects, Pi resource use, monthly cost, and maintenance burden.
2. Treat Milestones 0-2 as the launch target. Do not block launch on AI generation, WebSockets, voice control, substitutions, or Azure Functions.
3. Require a named user problem and success measure before moving a `Later` or `Conditional` backlog item into implementation.
4. Limit work in progress to one primary product slice plus necessary platform/risk work.
5. Remove or archive experiments that are not used after an agreed observation period.

**Acceptance criteria:**

- Every promoted backlog item links to its decision record, requirement change, implementation packet, and success measure.
- Optional work cannot silently become a dependency of the non-AI core.
- The project can stop after Milestone 2 and remain a useful, supportable household application.
- Generated recipes and WebSockets receive separate go/no-go decisions rather than inheriting approval from their broader milestones.

**Pi/cost impact:** This is a process control intended to reduce both resource use and personal maintenance cost.

**Open questions:**

- Who makes the final scope decision when household preferences differ?
- How long should an implemented experiment remain before judging adoption?
- What maintenance budget, in hours per month, is acceptable in addition to the USD cost ceiling?
- Which currently committed requirements should become optional if Milestones 0-2 take longer than expected?

## Suggested sequencing

The backlog should not become a second all-at-once implementation plan. Use this order unless household evidence changes it:

1. Resolve BL-015 and include BL-016 foundations during Milestone 0.
2. Run BL-001 while completing the manual cookbook and plan/shop/cook workflow.
3. Implement BL-002 from observed pantry friction and BL-019 before any production AI use.
4. Prioritize BL-003, BL-004, BL-005, and BL-006 according to shopping/planning observations.
5. Include BL-012 in assisted import, then add BL-008 and BL-013 if imports are used repeatedly.
6. Complete BL-009 and BL-014 before deciding whether generated recipes add value.
7. Decide BL-017 from actual concurrent/offline usage; do not assume WebSockets are required.
8. Consider BL-007, BL-010, and BL-011 only after repeated user requests.
9. Evaluate BL-018 only after a local-worker benchmark crosses its trigger.
10. Apply BL-020 at every release gate.

## Promotion checklist

Before moving any backlog item into the committed implementation plan, answer its open questions or record explicit assumptions, then provide:

```text
Backlog ID and decision record:
Observed user problem and evidence:
Selected scope and explicit non-goals:
Requirement/API/domain changes:
Owning implementation packet and dependencies:
Acceptance tests and success measure:
Privacy/security implications:
Pi resource budget:
Azure cost ceiling and disablement behavior:
Review date or removal criterion:
```

If an item changes persisted data, public API behavior, authentication, privacy, or recovery semantics, update the relevant contract documents before implementation begins.