# RedCollar — Fire-Protection Inspection SaaS

RedCollar is a vertical SaaS compliance engine built specifically for 1–10 technician fire protection shops. It functions as a comprehensive "compliance brain," handling NFPA scheduling, field inspections, AHJ reports, deficiency pipelines, service agreements, and invoice management.

## Project & Hosting Overview

- **Specification & Pricing:** Located in `projects/firesafety/SPEC.md`. Pricing is LOCKED to device-banded flat tiers (Solo, Crew, Shop, Pro) — **NEVER per-user**. Name is LOCKED as RedCollar. Approved domains are `redcollarfire.com` and `redcollarhq.com` (purchase deferred by Stan). Placeholder contact email: `stan@greencollarindustries.com` [2026-08-17].
- **Environments:** Development Convex deployment (`https://dynamic-alpaca-765.convex.cloud`) and Production Convex deployment (`https://robust-dolphin-878.convex.cloud`). **NEVER deploy production without Stan's explicit approval** [2026-08-17].
- **Tech Stack & Hosting:** React/Vite + Convex web app. Hosted on the legacy platform, with planned migration to Stan's own GitHub + Firebase Hosting + Convex account. Workspace volume packages can occasionally vanish; resolve via `rm -rf node_modules && bun install`.
- **Authentication & Multi-tenancy:** Auth providers restricted to `email_password` only (no legacy platform branding permitted). Multi-tenant architecture with companies and memberships; every record carries `companyId`. Tenancy helpers reside in `convex/tenant.ts` (`requireMembership`, `assertCompany`).
- **PWA & Caching:** Installable Progressive Web App (`public/manifest.webmanifest`) with a service worker (`public/sw.js`) utilizing cache-first asset management. Stamped with `redcollar-__BUILD_ID__` via Vite `closeBundle` plus `reg.update()` / reload on `controllerchange` to prevent stale browser builds.

## Data Model & NFPA Catalog (`convex/nfpa.ts`)

- **Core Entities:** `companies`, `members`, `buildings`, `systems`, `devices`, `tasks`, `inspections`, `deficiencies`, `quotes`, `invoices`, `agreements`, `companySecrets` (securely storing per-company API keys with masked client-side visibility via `stripeStatus`), and `followUps`.
- **NFPA Catalog (`convex/nfpa.ts`):** 
  - *NFPA 10:* Extinguisher device types and tasks.
  - *NFPA 72:* Alarm devices (`convex/alarmRecord.ts`).
  - *NFPA 25:* Sprinkler systems (`convex/sprinklerRecord.ts`) including wet riser (`sprk_riser_wet`: `sprk_quarterly` 3mo, `main_drain_annual`, `sprk_internal_5yr`), dry valve (`sprk_dry_valve`: `low_point_annual`, `dry_trip_3yr`), control valve (`sprk_control_valve`: `valve_quarterly`, `valve_exercise_annual`), waterflow device (`waterflow_device`: `waterflow_semi`), fire pump (`sprk_fire_pump`: `pump_churn_monthly`, `pump_flow_annual`), and antifreeze loop (`sprk_antifreeze_loop`: `antifreeze_annual`).
  - *NFPA 17A/96:* Kitchen hood systems (`hood_system` etc., semi-annual tasks, rates).
- **Device State:** Each task defines `{key, label, frequencyMonths, codeRef}`. `Device.lastCompleted` holds a record of `taskKey` to timestamp (ms); `nextDue` calculates as `lastCompleted + frequency`, with never-done devices defaulting to due immediately.

## Core Workflows & Logic

- **Schedule & Inspections (`convex/schedule.ts`, `convex/inspections.ts`):** 
  - Dashboard tracks overdue items, 90-day due lists, and a 12-month revenue calendar. 
  - Inspection flow initiates via building, kind, and task keys, records item statuses (pass/fail/N/A, where failures auto-create deficiencies), and completes by rolling passed tasks forward. 
  - Printable reports are available at `/inspections/:id/report` via `window.print()`. 
  - *UI Navigation Gotcha:* In the inspections list view, table rows are navigated by clicking the date cell. Inspections `list` sorts `startedAt` descending.
- **Compliance Records:** 
  - *NFPA 72 §7.8 Alarm Records:* Captures monitoring company info, notified/restored timestamps, battery readings, and drawn PNG signatures. `updateAlarmRecord` supports partial patches (null clears). Rendered via `AlarmRecordCard.tsx` and printed on report blocks.
  - *NFPA 25 Sprinkler Records:* Captures `valvesConfirmedOpen`, main drain static/residual PSI (UI shows PSI drop), waterflow alarm operated, monitoring notified timestamp, system restored timestamp, antifreeze specific gravity, and signatures. `updateSprinklerRecord` mirrors alarm records. `SprinklerRecordCard.tsx` mirrors alarm cards, and report pages read `sigRec` (`alarmRecord` | `sprinklerRecord`).
  - *Main Drain Trends:* `MainDrainTrendCard` on `BuildingPage` (`inspections.mainDrainHistory`) displays drop charts and tables with amber warnings when residual pressure falls >10% vs prior test (NFPA 25 §13.2.5.3).
- **Agreements & Billing:** Agreements manage device-scoped task catalogs, billing cadences, and anchor start dates (`billingCadence`, `billingStartAt`). *Bug fixed:* anchoring at startAt back-billed a year; anchor is now set when cadence is first enabled. `generateAgreementInvoices` generates bills. Note: agreements must be scoped per *device* rather than per *building* to prevent new-trade devices from disappearing from the schedule.
- **TCE Filing Queue:** Located at `/tce`. Tracks `buildings.tceRequired`, `tceStatus`, `tceSubmittedAt`, and `tceConfirmation` on inspections, flagging unsubmitted items >10 days with a red badge. `createInvoiceFromInspection` automatically appends a "Compliance reporting filing fee (TCE)" when `tceRequired` is true (`priceBook` `tce_filing_fee` flat, default $15).
- **Quotes & Invoices:** Invoices #5001+ (`createInvoiceFromInspection` is idempotent per visit; `createInvoiceFromQuote` resolves deficiencies upon payment). Deficiency multi-select generates quotes #1001+ with editable line items and statuses (draft, sent, accepted, declined, invoiced, paid). Printable quotes/invoices reside at `/quotes/:id`.
- **Aging & Follow-ups (`convex/followups.ts`):** Tracks AR aging buckets (0–30, 31–60, 61–90, 90+ days based on sentAt/issuedAt) and stale quotes (≥14 days). Dashboard summaries use uniform whole-day `Math.floor` calculations to eliminate boundary count discrepancies. UI includes the `/followups` page, sidebar "Follow-ups" (BellRing), and a dashboard rail card.

## Payments & Delivery

- **Resend API:** Used for transactional invoice email delivery (`RESEND_API_KEY` and optional `RESEND_FROM`). Falls back to `mailto` links with payment URLs if no key is configured. (Note: The platform email API is auth-only for OTP/magic links/verification, so Resend is required for invoices).
- **Online Payments Status:** Online card payments via Stripe were disabled by Stan [2026-08-18]. The UI now uses view/print invoices, receipts, and manual payment logging (`convex/payments.ts` Stripe functions remain dormant for future re-enablement).
- **Public Pay Page:** `/pay/:token` `PayInvoicePage` (public route above `ProtectedRoute` since customers are not internal users). Displays paid banners, payment methods, and view/print receipts.
- **Landing Page:** Pricing and tiers (Solo, Crew, Shop, Pro) live on the logged-out landing page (`LandingPage.tsx` = public `/` when auth enabled; `PublicAppRoutes`/`PublicLandingPage` are dead code). Contact uses mailto links.

## Virtual-Shop Simulation (Dev Convex Only)

- Populates 10 fictional `[SIM]`-tagged tenants with a backdated year of operations (thousands of devices, inspections, and quotes) using `convex/simSeed.ts` (internal functions: `wipeSimBatch` loop, `loadCompanyCore`, `loadHistoryChunk`, `loadQuotes`, `simStats`, `simSwitchAgentCompany`, `simSwapOwner`).
- Generators and loaders (`scripts/sim-gen.ts`, `scripts/sim-load.ts`) use admin `ConvexHttpClient` via `setAdminAuth(deploy key)` to bypass request argv size limits.
- **Convex Limits Solved:** 1024 fields/object (return arrays not records), 4096 reads (batch wipes), 32k docs read (use denormalized counters in stats).
- **Sim Owner Logins:** `owner-shop01..10@test.local` / `SimShops2026!` (must use `@test.local` EXACTLY; subdomains fail `testAuth`).
- **Reports & Iterations:** Findings reports in `projects/firesafety/research/virtual-shops-report.md` and `.pdf`, plus reruns SIM_V3, SIM_V4/V5, and SIM_V7 (`simSeed` functions like `sprint2Backfill`, `sprint3Hood`, `sprint3Tce`, `sprint4Sprinkler`, `sprint5DrainDemo`, `sprint6QuoteDates`, `sprint6Adoption`, etc.).

## Market & Compliance Research

- **Market Context:** Incumbent software charges $99–180/user; flat-fee rivals include FireLab ($299–499/mo) and FireFlow ($99/mo flat, thesis clone). RedCollar differentiates on compliance depth (AHJ forms, TCE filing) rather than price.
- **Research Files:** Stored in `research/` (competitor-teardown, nfpa-forms-research, discovery-kit in `research/raw/`).
- **Ohio State Fire Marshal (SFM) Roster:** Sourced from public records (`elicense7.com.ohio.gov`, CSVs and `ohio-fire-shops.xlsx` in `research/roster/`). Identifies 1,092 Ohio shops with 1–10 technicians (~290 core fire TAM, 42 near Coshocton, phones for 98%). Linked by license number only; state sources are never explicitly named in the UI ("state records"). Informal shop conversations kept by Stan (Wagner Coshocton, Bower Mount Vernon, Ohio Firewatch Millersburg).

## Testing & E2E

- Automated flows in `scripts/redcollar-flow.ts` and `scripts/redcollar-m2.ts`. If chromium is missing, install via `bunx playwright install chromium chromium-headless-shell`.

## Feature Status

- **Shipped Features [2026-08-18]:**
  - M1 Schedule engine, dual trades (extinguisher + alarm), inspections, deficiencies, and printable reports.
  - M2 QR tagging (`assignTags`, `tagSheet`, `resolveTag`, scan route `/t/:tagCode`, printable sheets `/buildings/:id/tags`) and installable PWA.
  - M3 Quote pipeline (deficiency multi-select, editable line items, status workflows).
  - Agreements, CSV bulk import (`/import`, `importRows` ≤200/batch), price book management, and NFPA 72 alarm records.
  - NFPA 17A/96 kitchen hood support and TCE filing queue at `/tce` with overdue badges.
  - NFPA 25 sprinkler inspection records and main drain trend analytics.
  - Invoice delivery workflows, public token payment pages (`/pay/:token`), and AR aging follow-up dashboard.
- **Not Built Yet:** True offline mutation sync, automated Stripe card checkouts, state roster automated import/license-lapse alerts, help center, automated TCE export queues, and NFPA 25 sim adoption runs.

## Gotchas & Lessons Learned

- **Dependencies:** Local workspace `node_modules` can occasionally vanish; resolve via `rm -rf node_modules && bun install`.
- **UI Navigation:** In the inspections list view, table rows are navigated by clicking the date cell.
- **Service Worker Stale Builds:** Cache-first service workers can strand users on outdated frontend builds; always stamp `redcollar-__BUILD_ID__` during Vite builds and trigger controller change reloads.
- **Convex Limits:** Avoid large object returns (>1024 fields) by returning arrays; manage large batch operations to stay under document read thresholds.
- **Test Accounts:** Simulation owner logins require `@test.local` domains precisely; subdomains fail `testAuth`.
- **Agreement Scoping:** Agreements must be scoped per *device* rather than per *building* to prevent new-trade devices from disappearing from the schedule.
- **House Rules:** Never mention the legacy platform name anywhere; custom domains must lock the address bar; state data sources must never be named in the UI ("state records"); link by license number only.

## Open Items

- Live production testing of Resend transactional email delivery using a valid API key.
- Future custom domain purchase (`redcollarfire.com` / `redcollarhq.com`) and migration to Stan's GitHub + Firebase Hosting + Convex infrastructure.
- Implementation of automated license-lapse alerts via state roster data.
- Development of full offline sync capabilities.
