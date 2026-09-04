# WellCollar SaaS Knowledge Base

WellCollar is a multi-tenant back-office SaaS platform built for independent oil and gas operators across Ohio, Pennsylvania, and West Virginia. It provides a comprehensive system for lease and well management, production tracking, revenue distribution, joint interest billing (JIB), state regulatory compliance, field operations, and accounting integrations.

## Overview & Positioning

- **Branding & Naming:** Formerly known as WellWorks, the platform was rebranded to **WellCollar** with the lockup "by Green Collar Industries" (chosen by Stan on 2026-08-13 because "WellWorks" was taken). The application name lives in `APP_NAME` (`src/lib/constants.ts` and `convex/constants.ts`). The platform project identifier remains `wellhead` and must not be renamed.
- **Business Goal:** Sell and license the SaaS platform to external independent oil and gas operators [2026-08-12].
- **Domains & Access:** The custom domain `www.wellcollar.com` is LIVE (root `wellcollar.com` 302-forwards to `www` via Squarespace Domain Forwarding; registrar is Squarespace; CNAME records point to the hosting gate with required verification TXT records set) [2026-08-15]. The site is protected by authentication; visitors hit the login gate until access is opened.
- **Positioning:** "Your whole operation, one platform" / "Your entire operation, ready on day one."

## Pricing, Onboarding, & Business Rules

- **Pricing Tiers (Locked by Stan [2026-08-14]; supersedes all earlier proposals):**
  - **Pumper:** $95/mo flat (≤25 wells)
  - **Field:** $295/mo flat (≤100 wells)
  - **Operator:** $595/mo flat (≤300 wells)
  - **Operator Pro:** $2/well/mo for 300+ wells (minimum $600/mo)
- **Onboarding Fees (Mandatory when applicable, never waived):**
  - Pumper: $0 self-serve ($250 optional white-glove)
  - Field: $750
  - Operator: $1,500
  - Operator Pro: $2,500
- **Stan's Standing Rules:** No prepay or annual discounts of any kind; never waive onboarding fees; no per-user fees; flat fees only (never hourly); all modules included in every tier.
- **Market Sizing (Ohio):** Based on ODNR RBDMS Well table (`WL_STATUS='PR'`, `OPNO`), there are 6,116 operators across 60,978 producing wells (1–5 wells: 5,580 ops; 6–10 wells: 133 ops; 11–25 wells: 143 ops; 26–100 wells: 156 ops; 101–300 wells: 74 ops; 300+ wells: 30 ops) [2026-08-14].
- **Payments:** Online checkout is disabled (`PAYMENTS_DISABLED = true` in `convex/stripe.ts`); Stan handles activation and billing personally. All landing page `/signup` call-to-action buttons route to the contact section (`ContactSalesForm`). Leads are stored in the `leads` table and trigger email alerts via `internal.notify.sendEmail`.

## Tech Stack, Architecture, & Deployment

- **Stack:** React + Vite frontend (using `react-router` v8, not `react-router-dom`) backed by a Convex real-time database and backend functions.
- **Multi-Tenancy:** Implemented via `companies` and `memberships` tables. Every table carries `companyId`, and all backend functions use `authenticatedQuery` or `authenticatedMutation` coupled with `assertCompany`.
- **Deployment Constraints:** Vercel rate limits deployments to **20 app deploys per day** across environments. Always batch code changes before deploying. Run local TypeScript checks using strict tooling (`tsgo` or compiler checks) prior to deploying.
- **Admin HTTP Routes:** Secret-gated HTTP admin endpoints reside on `<convex.site>/admin/*` (defined in `convex/adminHttp.ts`, secured by `ADMIN_SECRET`). Endpoints include operations for importing wells, setting acquisition dates, importing compliance events and scout data, syncing state data, running ODNR and land imports, resetting demo environments, backfilling user numbers, and rekeying compliance keys.

## Data Model & Portability

- **Core Tables:** `companies`, `memberships`, `wells` (unique import-upsert key: API number), interest owners, DOI decks, revenue distribution runs, JIB runs, `complianceEvents`, `scoutViolations`, `scoutOperatorStats`, `tanks`, `routes`, `landAgreements`, `purchasers`, `vendors`, `gasMeters`, `haulCallIns`, `runTickets`, `workOrders`, `miaInspections`, `signinEvents`, `leads`, `invoiceLines`, `invoicePayments`, `invoices`, `copasSettings`, `ppaRuns`, `ownerTaxDocs`, `auditLogs`, `invites`, and `timeclock`.
- **Decks & Revenue:** Deck decimals are NRI-style revenue decimals summing to 1.0 per well. Owner net payout equals `decimal × (gross − severance tax − deductions)`. JIB allocates expenses to Working Interest (WI) owners pro-rata by normalized WI decimal.
- **General Ledger:** There is no in-app GL; financial synchronization is designed around planned QuickBooks Online integration.
- **Data Portability & Backups:** 
  - Export endpoint `convex/adminExport.ts` (`exportAll`) outputs 20 tables (including tanks, routes, land agreements, purchasers, vendors, gas meters, haul call-ins, etc.), secured by `BACKUP_EXPORT_SECRET`.
  - Automated backup scheduled jobs export frequent JSON data and daily source archives/CSVs to cloud storage (e.g., Google Drive folder `Green Collar Energy/Backups`).

## State Regulatory Integration (OH, PA, WV)

- **Secrecy Rule (Critical):** Never reveal state regulatory agencies (ODNR, PA DEP, WV DEP) as data sources anywhere in user-facing software, help articles, or Nikki responses. Always refer to them neutrally as "state records" or "official state data". Scanned state documents are proxied via secure HTTP actions (`convex/stateDocProxy.ts` / `/state-doc`) allowlisted to official download endpoints.
- **Ohio (OH):** Public well database queryable via ArcGIS REST APIs (`gis.ohiodnr.gov`) without keys. Form 10 (annual statement of production) is due March 31 (ORC 1509.11). Weekly RBDMS database exports (`Rbdmsd97Weekly.zip`) supply inspection and violation records (`tblInspection`, `tblInspFail`, `tblInspFlDesc`). Chief's Orders (`OCN-YYYY-NN`) are parsed from inspector comment text. Class II Disposal wells in the system include McKeon F (#4, API 34-055-20773) and Numan Edwin A & Norma A (#13, API 34-151-22783).
- **Pennsylvania (PA):** State-profile architecture (`convex/stateProfiles.ts`). Annual production/waste reports and MIA annual reports are due Feb 15. MIA quarterly inspections are required for operating wells (§78.88, 5-year record retention). Inactive status is governed by §3214 (12-month abandonment presumption). Bonds are $2,500/well or a $25k blanket + $1k per new well (Act 96). Brine road spreading is unauthorized; waste-stream tracking is used instead. Static registry shards reside under `public/registry/pa/`.
- **West Virginia (WV):** Added [2026-08-24]. WR-39 annual production reports are due March 31 (W. Va. Code § 22-6-22). Bonds are $5k/well or $50k blanket. 12-month abandonment presumption applies. Static registry shards reside under `public/registry/wv/`.

## Modules & Special Features

- **Acquisition Scout:** Enables operator searches, live state ArcGIS well queries, and automated portfolio briefing generation (`convex/scoutReport.ts`, producing print-ready letter HTML tailored to target operators).
- **Field Operations & PWA:** Offline-capable progressive web app (`/field`) with a localStorage queue (`ww-field-queue`), strap-to-barrel conversion (`strapToBbl`), gauge-drop ticket prompts, water-haul locks, fluid transfers, well tests, downtime tracking, an automated alert engine (`convex/alerts.ts`), custom variables formula engine, and a public REST API (`/api/v1/...`).
- **Compliance & Inspections:** Tracks inspection and violation lifecycles, open inspection failure descriptions, Chief's Orders (`OCN`) badges, and a comprehensive state compliance forms reference library.
- **MIA / Well Integrity:** Quarterly well integrity inspections, casing pressure logging (permitting "no gauge/not readable" selections with notes), and automatic creation of work orders for leaks or gas issues.
- **Haul Call-Ins & Run Tickets:** Single-tank or Split-Load haul call-ins from tank batteries, automatic linking to oil/brine run tickets, and variance flagging (`|Δ| ≥ 5 bbl` and `≥ 10%`). Ergon run tickets are imported and matched to wells by API numbers.
- **Land Management:** Tracks agreements (leases, ROW, SWD, surface, unit, other), recurring obligations, lease risk alerts, assignments, successors, and record-release obligations.
- **Invoicing, A/R, & Tax:** Manages invoices (JIB, AFE, General/Work Orders), Purchaser registry, A/P Vendors, 1099-MISC/NEC reporting, and QuickBooks IIF file generation.
- **House Gas & Tanks:** Landowner gas usage billing with yearly MCF allowances and overage calculations, alongside a tank battery registry with well association rules and stock tank exceptions.
- **AI Assistant (Nikki):** Floating chat widget mounted on every page. Implements help-first keyword search over help articles (`src/lib/helpSearch.ts`) for zero-cost local answers, with manual escalation to the agent bridge for administrators.

## Team, Roles, Auth, & Users

- **Roles & Permissions:** Four roles are supported: `admin`, `manager`, `pumper`, and `viewer`. Role enforcement is handled via `getCompanyForUser(ctx, userId, minRole?)`.
- **Audit Logging:** An `auditedDb` wrapper automatically logs all insert, patch, replace, and delete operations to the `auditLogs` table (excluding auth tables).
- **Team Management & Seat Limits:** Supports team invites and manual user creation (`team.createUser`). Seat limits are enforced per pricing tier (Pumper: 3 seats; Field: 6 seats; Operator: 12 seats; Operator Pro: unlimited).
- **User Numbering:** `memberships.userNo` stores sequential numeric IDs (rendered as "U101", "U102", etc.) starting at 101, which are never reused.
- **Authentication:** Supports legacy OAuth login and email/password authentication. Accounts created via OAuth lack a password; users can sign up with the same email via OTP to link their credentials securely.

## Outreach & Prospecting Campaigns

- **Outreach Tracks:** Automated daily outreach briefing batches targeting independent operators (e.g., the 26–99 well segment, the 100+ well segment, and PA operators). Progress is tracked via dedicated Google Sheets.
- **Briefing Rules:** Never mention state data sources in prospect materials (use the framing: "During onboarding, we build your complete portfolio into the software before your first login"). Always highlight the auto-filing of mandated state reports and match the locked pricing tier to the operator's well count. Include CAN-SPAM footers and enforce bounce-handling protocols.

## Gotchas, Operational Rules, & Lessons Learned

- **Well Matching Rule:** Link external records (spreadsheets, run tickets, documents) to system wells by **API number only** (matching the first 10 digits for partials). Never modify internal well names to match external sources; skip unlisted wells entirely.
- **Non-Destructive Backfills:** Admin backfill mutations matching records by key (such as API numbers) must default to `skipIfAlreadySet: true` (only write if the target field is empty, unless explicitly commanded otherwise).
- **Null Values in Payloads:** Convex mutation validators reject `null` values. Optional fields must be omitted entirely from request payloads.
- **Test Isolation:** Automated test scripts must scope selectors (such as matching table rows by date or text) to prevent cross-tenant pollution on shared demo companies.
- **Concurrency Hazards:** Multiple developers and background workers share a single repository checkout. Always run `git status` and verify changes before committing or deploying.
- **Date Inputs:** Always use the custom `DateInput` component (`src/components/ui/date-input.tsx`) instead of raw `input type="date"` elements to ensure calendar buttons render correctly across all forms.

## Roadmap & Open Items

- Statement PDF auto-parsing.
- Owner suspense carry-forward across revenue runs.
- 1099 export generation enhancements.
- Expanded Ohio state regulatory reporting automation (Form 10).
- Full QuickBooks Online (QBO) synchronization implementation (OAuth flow, account mapping, sync logs).
- Stripe billing re-evaluation in live mode.
- Owner portal and marketing site expansions.
