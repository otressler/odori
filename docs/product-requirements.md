# Product requirements

## Product vision

Odori helps a household decide what to cook, remember recipes, and buy only what is needed. It must feel like a warm kitchen notebook rather than an inventory-management system. The primary user is a home cook using a phone or tablet in the kitchen; the application is private to the user's Tailscale network.

## Goals and boundaries

**Goals**

- Import a recipe from a URL, photo, or PDF and turn it into an editable structured recipe.
- Maintain a low-friction inventory using availability states instead of quantities.
- Recommend recipes using available ingredients and recently cooked meals.
- Plan meals in a weekly calendar and generate a consolidated shopping list.
- Move checked-off purchases into inventory without duplicate entry.
- Keep a recipe page awake while cooking.
- Collaborate on inventory and shopping lists without conflicting silent updates.

**Out of scope for the initial release**

- Nutritional, allergen, price, barcode, or expiry-date tracking.
- Precise stock quantities, unit conversion, multi-store optimization, and household collaboration controls.
- Public accounts, public sharing, or operation without Tailscale.
- Autonomous purchase ordering.

## Personas

| Persona | Need |
| --- | --- |
| Home cook | Quickly capture recipes and cook from a dependable, readable view. |
| Meal planner | Arrange familiar and suggested meals over a week, then obtain one practical list. |
| Shopper | Check off purchases on a phone and have pantry availability update automatically. |

## Core user journeys

### Import and curate a recipe

1. The user submits a recipe URL or uploads an image/PDF.
2. The system creates an import job and shows progress.
3. A URL is fetched and parsed; a document is processed with Azure AI Document Intelligence.
4. An LLM converts source text into the recipe schema, normalizes ingredient names, and proposes categories.
5. The user reviews and corrects title, servings, ingredients, and steps before saving to the catalog.
6. The source and extraction confidence remain traceable to support correction and reprocessing.

### Plan, shop, and cook

1. The user drags catalog recipes or suggestions onto days and meal slots in a week.
2. The app calculates ingredients required by the plan and omits ingredients whose inventory status is `in_stock`.
3. The user creates or refreshes a shopping list; manually added items are preserved.
4. Checking off an item marks it purchased and sets its mapped inventory item to `in_stock`.
5. Opening a planned recipe starts Kitchen Mode, which can request a screen wake lock.
6. Marking a meal cooked records history; this reduces the score of recently cooked recipes in future suggestions.

### Protect planned ingredients

1. The plan derives a current set of inventory ingredients required by upcoming meals.
2. When a user manually changes a required `in_stock` item to `needs_replenishment`, the app warns that one or more upcoming meals need it and names those meals.
3. The user may cancel or explicitly confirm the change; the confirmed change is recorded in history.
4. When the change originates from cooking its linked planned recipe, the app updates inventory without a warning because that consumption is expected.

### Collaborate while shopping

1. Household members open the same inventory or shopping list.
2. Each connected client joins the list or household real-time channel after authenticated authorization.
3. A member's item edit, purchase, status change, or list regeneration is immediately reflected for every viewer.
4. Conflicting stale edits are rejected with the current state rather than overwriting another member's change.

## Derived user stories

1. **As a cook**, I want to review and correct imported recipes before publishing them, so incorrect AI extraction never pollutes my catalog.
2. **As a cook**, I want recipes to retain their original ingredient wording while mapping to shared ingredient tags, so recipes stay readable and shopping stays consolidated.
3. **As a planner**, I want to adjust servings per planned meal, so the shopping list matches the household meal.
4. **As a planner**, I want planned meals to flag recent repeats, so the weekly menu has more variety.
5. **As a shopper**, I want to add and check off manual items, so one list works for both meal ingredients and household necessities.
6. **As a shopper**, I want each calculated shopping entry to show the recipes that need it, so I can decide whether to skip it.
7. **As a cook**, I want to mark an item as unknown rather than out of stock, so I can avoid maintaining precise pantry quantities.
8. **As a cook**, I want Kitchen Mode to preserve my screen state and provide timers, so I can follow recipes hands-free and without screen dimming.
9. **As a user**, I want recommendations to explain their inventory match and missing ingredients, so I can trust and compare them.
10. **As a user**, I want generated recipe ideas saved as drafts, so I control what enters my permanent catalog.
11. **As a household user**, I want inventory and shopping updates to avoid overwriting another device's changes, so shared use remains reliable.
12. **As an operator**, I want backups covering both database and original recipe uploads, so the household cookbook can be recovered after device failure.
13. **As a planner**, I want a warning before I remove an ingredient required by upcoming meals, so I do not accidentally invalidate my plan.
14. **As a cook**, I want the inventory change caused by cooking a planned recipe to happen without a warning, so expected consumption does not interrupt cooking.
15. **As a household member**, I want shared lists and inventory to update live while we view them together, so we do not buy or change the same item twice.

## Functional requirements

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-01 | Import image and PDF recipes through Azure AI Document Intelligence. | The system stores an import job, source metadata, raw extracted text, and a structured draft or actionable failure. |
| FR-02 | Import recipes from a URL. | The system fetches permitted public pages, extracts readable recipe content, and presents a reviewable draft. |
| FR-03 | Normalize ingredients using an LLM. | Each ingredient retains its source text and has a canonical inventory tag plus a confidence or review state. |
| FR-04 | Maintain a recipe catalog. | Users can create, edit, archive, search, tag, and open recipes with ingredients, servings, and ordered steps. |
| FR-05 | Track coarse inventory. | An inventory item can be `in_stock`, `needs_replenishment`, or `unknown`; no quantity is required. |
| FR-06 | Suggest recipes using stock and history. | Suggestions identify ingredient coverage, missing items, and a reason; users can save a generated suggestion as a catalog recipe. |
| FR-07 | Create a weekly meal plan. | Users can assign, move, remove, and resize recipe servings for meal slots in a selected week. |
| FR-08 | Generate shopping lists from a plan. | The list aggregates planned ingredients, excludes `in_stock` items by default, and retains manual items when regenerated. |
| FR-09 | Update inventory from purchases. | Checking off a list item records the purchase and changes its linked inventory item to `in_stock`. |
| FR-10 | Provide Kitchen Mode. | A cooking view has large readable steps, progress controls, timers, and requests a Wake Lock when browser support and user permission allow it. |
| FR-11 | Deploy privately. | The app is containerized, routed through Traefik, and not exposed by host ports to the public internet. |
| FR-12 | Warn about removal of planned stock. | A manual status change away from `in_stock` warns when an upcoming meal requires the item; explicit ingredient changes selected while cooking that planned recipe do not produce this warning. Marking cooked alone does not infer depletion. |
| FR-13 | Support household collaboration. | Authorized users can share inventory and lists, and active viewers receive authenticated real-time item/list updates. |
| FR-14 | Support recipe curation. | Users can edit extraction results, tag, favorite, archive, search, and scale recipes while preserving their source and original ingredient lines. |
| FR-15 | Preserve shopping-list intent. | Users can add manual household items, see recipe provenance for calculated items, skip entries, and retain manual/purchased/skipped entries through regeneration. |
| FR-16 | Support inclusive meal planning. | Users can use meal slots, serving changes, leftovers/notes, and touch-accessible controls; repeat meals are visibly identified. |
| FR-17 | Make recommendations explainable. | Suggestions identify matched and missing ingredients, account for recent meals and planned duplicates, and label generated recipes as drafts. |
| FR-18 | Support data portability and recovery. | The household can export its approved recipes and operational backups include database and uploaded source files. |

## Non-functional requirements

| Area | Requirement |
| --- | --- |
| Responsive UX | Support current mobile, tablet, and desktop browsers; planning requires pointer and touch-friendly move controls, not drag-and-drop alone. |
| Performance | Typical catalog and weekly-plan views should render within 2 seconds on the local Tailscale network; imports and AI work run asynchronously. |
| Reliability | Import failures must be visible and retryable. No failed AI call may overwrite an approved recipe. |
| Privacy | Tailscale-only ingress, authenticated app access, encrypted provider API transport, and no telemetry containing recipe sources or household data. |
| Accessibility | Keyboard-operable primary flows, labelled controls, sufficient contrast, focus states, and semantic reading order. |
| Data integrity | Canonical ingredient tags use stable IDs; changing a display name must not disconnect recipes, inventory, or list items. |
| Collaboration | Real-time events must be authorized by household and resource, ordered/versioned per entity, and safely recoverable through REST refresh after reconnect. |
| Localization | Initial language is German; data model and UI strings must support additional locales. |

## Design direction: Tuscan Vintage

- **Colors:** terracotta `#E2725B` for primary actions, olive `#708238` for secondary/success states, crema `#FDFBF7` background, espresso `#2C211E` text.
- **Type:** Playfair Display for headings and Lora for body copy, with resilient system-serif fallbacks.
- **Surfaces:** rounded cards and buttons, subtle paper-grain treatment, low-contrast soft shadows, and ample spacing.
- **Usability constraint:** texture must remain decorative only; it cannot lower text contrast or obscure interactive state.
- **Collaboration constraint:** visible list updates must announce who made a relevant change without relying only on color or transient animation.

## Delivery phases

1. **Engineering foundation:** private Pi deployment, authentication, household isolation, migrations, diagnostics, CI, and ARM64 release path.
2. **Cookbook and pantry:** manual recipe curation, ingredient taxonomy, and status inventory. This is the first useful product and has no Azure dependency.
3. **Plan, shop, and cook:** weekly plan, aggregated shopping list, purchase-to-inventory flow, planned-stock warnings, Kitchen Mode, and cook history.
4. **Assisted import:** durable URL/file jobs, Document Intelligence extraction, LLM normalization, review workflow, retries, quotas, and audit data.
5. **Recommendations:** deterministic explainable ranking first, followed by optional generated recipe drafts that always require review.
6. **Collaboration and recovery:** membership/roles, WebSocket-backed shared inventory and shopping, stale-write handling, export, and tested restore.

The detailed dependencies, implementation-agent work packets, and release gates are defined in the [implementation plan](implementation-plan.md). Milestones through plan/shop/cook must operate without Azure; cloud-assisted features are optional and independently disableable.
