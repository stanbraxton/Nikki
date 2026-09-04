# RedCollar — fire-protection inspection SaaS

RedCollar is a vertical SaaS compliance engine built specifically for 1–10 technician fire protection shops. It functions as a comprehensive "compliance brain," handling NFPA scheduling, field inspections, AHJ reports, deficiency pipelines, service agreements, and invoice management.

## Summary
RedCollar digitizes the core workflows of small fire protection contractors, managing compliance schedules for fire extinguishers, alarms, kitchen hood systems, and fire sprinklers across commercial buildings.

## Business & pricing
- **Pricing Model:** LOCKED device-banded flat tiers (Solo, Crew, Shop, Pro) — **NEVER per-user**. 
- **Market Context:** Incumbent software charges $99–180/user; flat-fee rivals include FireLab ($299–499/mo) and FireFlow ($99/mo flat, thesis clone). RedCollar differentiates on compliance depth (AHJ forms, TCE filing) rather than price.
- **Branding & Domains:** Name LOCKED as RedCollar. Domains `redcollarfire.com` and `redcollarhq.com` are approved (purchase deferred by Stan). Placeholder contact email: `stan@greencollarindustries.com`.
- **Payments Status:** Online card payments via Stripe were disabled by Stan [2026-08-18]; UI now uses view/print invoices, receipts, and manual payment logging (`convex/payments.ts` Stripe functions remain dormant for future re-enablement).

## Architecture
- **Tech Stack:** A React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account). 
- **Environments:** Separate Development and Production Convex deployments. Never deploy to production without Stan's explicit approval [2026-08-17].
- **Authentication:** `email_password` providers only (no third-party branding).
- **Multi-tenancy:** Company and membership structure where every record carries `companyId`. Tenancy helpers reside in `convex/tenant.ts` (`requireMembership`, `assertCompany`).
- **PWA & Caching:** Installable Progressive Web App (`public/manifest.webmanifest`) with a service worker (`public/sw.js`) utilizing cache-first asset management. Stamped with `redcollar-__BUILD_ID__` via Vite `closeBundle` to prevent stale browser builds.

## Data model
- **Core Entities:** Companies, members, buildings, systems, devices, tasks, inspections, deficiencies, quotes, invoices, agreements, and `companySecrets` (securely storing per-company API keys with masked client-side visibility).
- **NFPA Catalog (`convex/nfpa.ts`):** 
  - *NFPA 10:* Extinguisher device types and tasks.
  - *NFPA 72:* Alarm devices (`convex/alarmRecord.ts`).
  - *NFPA 25:* Sprinkler systems (`convex/sprinklerRecord.ts`) including wet riser, dry valve, control valve, waterflow device, fire pump, and antifreeze loop.
  - *NFPA 17A/96:* Kitchen hood systems.
- **Device State:** Each task defines `{key, label, frequencyMonths, codeRef}`. `Device.lastCompleted` holds a record of `taskKey` to timestamp (ms); `nextDue` calculates as `lastCompleted + frequency`, with never-done devices defaulting to due immediately.

## Key logic
- **Schedule & Inspections (`convex/schedule.ts`, `convex/inspections.ts`):** Dashboard tracks overdue items, 90-day due lists, and a 12-month revenue calendar. Inspection flow initiates via building, kind, and task keys, records item statuses (pass/fail/N/A, where failures auto-create deficiencies), and completes by rolling passed tasks forward. Printable reports are available at `/inspections/:id/report` via `window.print()`.
- **NFPA Compliance Records:** Dedicated records (`alarmRecord` and `sprinklerRecord`) capture monitoring company notifications, restoration timestamps, battery readings, main drain PSI static/residual drops (with amber warnings for >10% drops per NFPA 25 §13.2.5.3), and drawn PNG signatures.
- **Agreements & Billing:** Agreements manage device-scoped task catalogs, billing cadences, and anchor start dates (`billingCadence`, `billingStartAt`). Invoices support automatic TCE filing fee pass-through ($15 flat). Deficiency multi-select generates quotes (`#1001+`), and paid invoices automatically resolve linked deficiencies.
- **Follow-ups & AR Aging (`convex/followups.ts`):** Tracks AR aging buckets (0–30, 31–60, 61–90, 90+ days) and stale quotes (≥14 days). Dashboard summaries use uniform whole-day `Math.floor` calculations to eliminate boundary count discrepancies.
- **Virtual-Shop Simulation:** `convex/simSeed.ts` and associated scripts populate 10 fictional `[SIM]` tenants with a backdated year of operations (thousands of devices, inspections, and quotes) utilizing admin-authenticated Convex HTTP client wrappers to bypass request limits.

## Integrations & APIs
- **Resend API:** Used for transactional invoice email delivery (`RESEND_API_KEY` and optional `RESEND_FROM`). Falls back to `mailto` links with payment URLs if no key is configured.
- **Ohio State Fire Marshal (SFM) Roster:** Sourced from public records (`elicense7.com.ohio.gov`, CSVs and `ohio-fire-shops.xlsx`) identifying 1,092 Ohio shops with 1–10 technicians (~290 core fire TAM). Linked in the application by license number only; state sources are never explicitly named in the UI ("state records").

## Status
- **Shipped Features [2026-08-18]:**
  - M1 Schedule engine, dual trades (extinguisher + alarm), inspections, deficiencies, and printable reports.
  - M2 QR tagging (`assignTags`, `tagSheet`, `resolveTag`, scan route `/t/:tagCode`, printable sheets) and installable PWA.
  - M3 Quote pipeline (deficiency multi-select, editable line items, status workflows).
  - Agreements, CSV bulk import, price book management, and NFPA 72 alarm records.
  - NFPA 17A/96 kitchen hood support and TCE filing queue at `/tce` with overdue badges.
  - NFPA 25 sprinkler inspection records and main drain trend analytics.
  - Invoice delivery workflows, public token payment pages (`/pay/:token`), and AR aging follow-up dashboard.
- **Not Built Yet:** True offline mutation sync, automated Stripe card checkouts, state roster automated import/license-lapse alerts, help center, and automated TCE export queues.

## Gotchas & lessons
- **Dependencies:** Local workspace `node_modules` can occasionally vanish; resolve via `rm -rf node_modules && bun install`.
- **UI Navigation:** In the inspections list view, table rows are navigated by clicking the date cell.
- **Service Worker Stale Builds:** Cache-first service workers can strand users on outdated frontend builds; always stamp `redcollar-__BUILD_ID__` during Vite builds and trigger controller change reloads.
- **Convex Limits:** Avoid large object returns (>1024 fields) by returning arrays; manage large batch operations to stay under document read thresholds.
- **Test Accounts:** Simulation owner logins require `@test.local` domains precisely; subdomains fail `testAuth`.
- **Agreement Scoping:** Agreements must be scoped per *device* rather than per *building* to prevent new-trade devices from disappearing from the schedule.

## Open items
- Live production testing of Resend transactional email delivery using a valid API key.
- Future custom domain purchase (`redcollarfire.com` / `redcollarhq.com`) and migration to Stan's GitHub + Firebase Hosting + Convex infrastructure.
- Implementation of automated license-lapse alerts via state roster data.
- Development of full offline sync capabilities.
