# WellCollar (ex-WellWorks) — oil & gas back-office SaaS

WellCollar is a multi-tenant back-office SaaS platform built for independent oil and gas operators across Ohio, Pennsylvania, and West Virginia. It provides a comprehensive system for lease and well management, production tracking, revenue distribution, joint interest billing (JIB), state regulatory compliance, field operations, and accounting integrations.

## Business & pricing
- **Positioning & Branding:** Formerly WellWorks; rebranded to WellCollar ("by Green Collar Industries") with custom domain `www.wellcollar.com` live via Squarespace forwarding [2026-08-13]. Decided to commercialize and sell to external operators [2026-08-12].
- **Pricing Tiers (Locked by Stan [2026-08-14]):** 
  - Pumper: $95/mo flat (≤25 wells)
  - Field: $295/mo flat (≤100 wells)
  - Operator: $595/mo flat (≤300 wells)
  - Operator Pro: $2/well/mo for 300+ wells (min $600/mo)
- **Onboarding Fees (Mandatory, never waived):** Pumper $0 self-serve ($250 optional white-glove); Field $750; Operator $1,500; Operator Pro $2,500.
- **Business Rules:** No prepay/annual discounts; no waiving onboarding fees; no per-user fees; flat fees only; all modules included across all tiers.
- **Payments:** Online checkout disabled (`PAYMENTS_DISABLED = true`); Stan handles activation and payment personally. Contact form leads store in `leads` table and trigger email alerts (`internal.notify.sendEmail`).

## Architecture
- **Stack:** A React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account) using `react-router` v8.
- **Multi-Tenancy:** Implemented via `companies` and `memberships` tables. Every table includes `companyId`, secured by `authenticatedQuery/Mutation` and `assertCompany`.
- **Backups & Portability:** Automated daily and frequent database/source backup scripts. Export endpoint `convex/adminExport.ts` (`exportAll`) outputs 20 tables, secured by `BACKUP_EXPORT_SECRET`.

## Data model
- **Core Tables:** `companies`, `memberships`, `wells` (unique import-upsert key: API number), `interest owners`, `DOI decks`, `revenue distribution runs`, `JIB runs`, `complianceEvents`, `scoutViolations`, `scoutOperatorStats`, `tanks`, `routes`, `landAgreements`, `purchasers`, `vendors`, `gasMeters`, `haulCallIns`, `runTickets`, `workOrders`, `miaInspections`, `signinEvents`, `leads`.
- **Multi-State Support:** `regState` ("OH", "PA", "WV") on companies. `convex/stateProfiles.ts` defines state-specific compliance rules, annual duties, and bond calculations. Well states are resolved via `wellStateCode`.
- **Decks:** NRI-style revenue decimals summing to 1.0 per well. Owner net calculation: `decimal × (gross − severance tax − deductions)`.

## Key logic
- **JIB & Revenue Allocation:** JIB allocates expenses to Working Interest (WI) owners pro-rata by normalized WI decimal. Revenue distribution runs calculate statement lines per owner, closing to write immutable allocation rows.
- **General Ledger:** No in-app GL; planned QuickBooks integration handles accounting sync.
- **Well Selection & Data Imports:** Standardized searchable combobox (`src/components/WellCombobox.tsx`) for API/name/county filtering. Non-destructive backfill rule (`skipIfAlreadySet: true` matching by API number) to preserve existing values. Paper records for unlisted wells are ignored.
- **Field Operations & Haul Call-Ins:** Supports single or multi-tank ("Split-Load") haul call-ins with strap-to-barrel conversion (`strapToBbl`). Ticket creation auto-closes matching pending call-ins. Entire field routes optimize tank visit sequences.

## Integrations & APIs
- **State Regulatory Data:** Direct integration with public ArcGIS REST APIs for Ohio (ODNR), Pennsylvania (PA DEP), and West Virginia (WV DEP) to sync well inventories and inspection/violation records. Scanned documents are proxied via state doc HTTP actions (`convex/stateDocProxy.ts`).
- **QuickBooks Online (QBO):** Client ID stored in environment variable `QBO_CLIENT_ID`; OAuth integration planned for one-way push of revenue and JIB data.
- **Admin Endpoints:** Secret-gated HTTP admin routes under `<convex.site>/admin/*` protected by `ADMIN_SECRET` in `convex/adminHttp.ts`.

## Status
- **Production & Domains:** Custom domain `www.wellcollar.com` live. Public access enabled with secure owner sign-in.
- **Shipped Modules:** Core wells, CSV import, interest owners, DOI decks, revenue distribution, JIB runs, dashboard, onboarding CompanyGate, Help Center, Routes, Command Center, Invoicing/AR with Purchasers, SOPs, House Gas + Tanks + Map, Multi-state Compliance (OH, PA, WV), Owner Payments, MIA Well Integrity, Haul Call-Ins, Field Route optimization, universal Nikki AI assistant chat, and automated daily outreach cron batches.
- **Milestones:** Rebranding completed [2026-08-13]; pricing locked [2026-08-14]; compliance rebuild shipped [2026-08-15]; PA expansion [2026-08-17]; WV expansion [2026-08-24].

## Gotchas & lessons
- **Data Secrecy Rule:** Never reveal ODNR, PA DEP, or WV DEP as data sources in user-facing UI or help materials; use neutral terms like "state records" or "official state data".
- **API Matching:** Link imported tickets and external records to wells by API number only (matching first 10 digits for partials). Never modify well names to match external sources.
- **Payload Validation:** Convex mutation validators reject `null` values; optional fields must be omitted entirely from payloads.
- **Egress Rate Limits:** State ArcGIS servers and web scrapers throttle or 403-block rapid requests; require pacing (e.g., 3-second delays) for bulk sync scripts.
- **Environment & Build Limits:** Convex CLI deploy target defaults (`CONVEX_DEPLOY_KEY`) require careful handling across environments. Vercel deploy limits cap builds at 20 per day.

## Open items
- Statement PDF auto-parsing.
- Owner suspense carry-forward across runs.
- 1099 export generation.
- Expanded Ohio state regulatory reporting automation (Form 10).
- Full QuickBooks Online (QBO) sync implementation (OAuth flow, account mapping, sync logs).
- Stripe billing re-evaluation.
- Owner portal and marketing site expansions.
