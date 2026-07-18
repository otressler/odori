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

## Non-functional requirements

| Area | Requirement |
| --- | --- |
| Responsive UX | Support current mobile, tablet, and desktop browsers; planning requires pointer and touch-friendly move controls, not drag-and-drop alone. |
| Performance | Typical catalog and weekly-plan views should render within 2 seconds on the local Tailscale network; imports and AI work run asynchronously. |
| Reliability | Import failures must be visible and retryable. No failed AI call may overwrite an approved recipe. |
| Privacy | Tailscale-only ingress, authenticated app access, encrypted provider API transport, and no telemetry containing recipe sources or household data. |
| Accessibility | Keyboard-operable primary flows, labelled controls, sufficient contrast, focus states, and semantic reading order. |
| Data integrity | Canonical ingredient tags use stable IDs; changing a display name must not disconnect recipes, inventory, or list items. |
| Localization | Initial language is German; data model and UI strings must support additional locales. |

## Design direction: Tuscan Vintage

- **Colors:** terracotta `#E2725B` for primary actions, olive `#708238` for secondary/success states, crema `#FDFBF7` background, espresso `#2C211E` text.
- **Type:** Playfair Display for headings and Lora for body copy, with resilient system-serif fallbacks.
- **Surfaces:** rounded cards and buttons, subtle paper-grain treatment, low-contrast soft shadows, and ample spacing.
- **Usability constraint:** texture must remain decorative only; it cannot lower text contrast or obscure interactive state.

## Delivery phases

1. **Foundation:** private deployment, authentication, recipe catalog, manual recipe entry, ingredient taxonomy, and status inventory.
2. **Planning:** weekly plan, aggregated shopping list, purchase-to-inventory flow, Kitchen Mode, and cook history.
3. **AI import:** URL/import jobs, Document Intelligence extraction, LLM normalization, review workflow, retries, and audit data.
4. **AI recommendations:** explainable ranking and optional generated recipes that always require review before catalog publication.
