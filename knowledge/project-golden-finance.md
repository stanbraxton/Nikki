# Golden Finance — Personal Finance App

Golden Finance is a comprehensive personal and business finance application built for Stan Braxton, handling spending, budgets, net worth, bills, charts, and automated statement imports.

## Summary & Hosting
- **Project Name:** `golden-finance`
- **Hosting & Domain:** Hosted on the legacy hosting platform, with a custom domain `www.mygoldenfinance.com` LIVE (root 302-redirect via Squarespace forwarding; CNAME and TXT configured at Squarespace DNS) [2026-08-15]. Slated for migration to Stan's own GitHub + Firebase Hosting + Convex account.
- **Access Control:** Public-facing routing with application-level owner sign-in protecting data. Workspace members edge access (`auth.mode: authenticated`).
- **Convex Databases:** Production Convex (`https://sincere-newt-681.convex.cloud`) uses a separate database from preview; data imported in preview does not appear in production. Import statements directly in production.

## Architecture & Data Model
- **Backend & Schema:** Built on React, Vite, and Convex (`convex/schema.ts`). Tables include accounts, transactions, budgets, bills, netWorthSnapshots, tasks, taskUsers, scenarioItems, goals, holdings, documents, and projects (all `userId`-scoped).
- **App Functions:** All backend functions in `convex/finance.ts` use `authenticatedQuery` or `authenticatedMutation`.
- **Permanent Admin Operations (`convex/adminOps.ts`, since 2026-08-13):** Secret-gated production endpoints replace obsolete temporary deploy patterns:
  - `exportAll` (query, dumps all 5 core tables)
  - `importRows` (mutation, de-duped transaction import)
  - `upsertBills` (mutation, upsert by name)
  - Secrets live in `scripts/.admin_secret`; IDs/URLs in `scripts/config.json`. Executed via Bun (`ConvexHttpClient` + `anyApi`).
- **Frontend & Routing:** Pages in `src/pages/` include Overview (`/dashboard`), Transactions, Import, Budgets, Accounts, Bills, Tasks, Subscriptions, Forecast, NetWorth, Debts, Goals, Tax Center, Investments, Document Vault, and Y2Y.
- **Amounts & Transfers:** Negative = money out, positive = money in. Liability accounts (`credit`, `loan`) store amounts owed and subtract from net worth. The `Transfers` category and internal transfers are excluded from income/spend totals to prevent card payment double-counting.
- **De-duplication & Months:** Imports de-dupe on `date|lowercased description|amount` (`by_user_dedupe`). Month pages default to the latest month with data (`useDefaultMonth`).

## Parsers & Statement Imports
- **PDF & CSV Parsing:** Statement PDFs are parsed entirely client-side via `pdfjs-dist` (lazy-imported, worker via `?url`); files never leave the browser. The parser reconstructs lines by y-position and matches `MM/DD desc amount`.
- **PNC Statements:** PNC Virtual Wallet statements group transactions in sections determining signs (deposits, checks, debit/online withdrawals, other deductions). Descriptions wrap and page-margin summary text is handled. Year is derived from the period header.
- **Business Statements (PNC Business Checking):** Uses `rowsFromPncStatement` with business section headers added to `PNC_SECTIONS`, noise-line filtering, and two-checks-per-row regex. *Gotcha:* Unrecognized section headers can silently flip transaction signs; always verify `PNC_SECTIONS` coverage.

## Scheduled Automations (Set up 2026-08-13)
- **Weekly Backup Scheduled Job:** Wed 6am ET / `0 10 * * 3` UTC via `scripts/gf_backup.py`. Exports all tables via `adminOps:exportAll`, writes JSON + CSVs + README, zips source, and uploads to Drive folder "Personal → Golden Finance Backups" (ID `1s-TnvWbvYrc9VhZpGVkMp3mfwKFyjD0S`), deleting superseded copies. Deploy-free.
- **Statement Auto-Import Scheduled Job:** Daily 7am ET via `scripts/gf_statement_autoimport.py` (skipped when no new files via `scripts/gf_autoimport_condition.py`). Watches Drive folder "Personal → Chase Statements" (ID `1UTQHGJf6LQUj2Noz2TYhgZjOPG_keCTd`), parses CSV/PDF, imports to prod, and DMs Stan a summary. Filename routing: "saving" → savings, "business" → business, else checking. State tracked in `state/processed_statement_files.json`.
- **Monthly Spending Report Scheduled Job:** 1st of month 8am ET. Pre-run script `scripts/gf_monthly_report_prerun.py` supplies last-month vs prior-month numbers for agent reporting to Stan.

## Financial Tasks & Add-on Modules (Built 2026-08-13)
- **Financial Tasks & Priorities:** Backend `convex/tasks.ts`, frontend `src/pages/TasksPage.tsx` (`/tasks`). Supports List and Board (kanban, HTML5 drag-drop + status select) views, task users, statuses (`todo`, `in_progress`, `under_review`, `completed`), priorities, monetary impact, recurring tasks with auto-spawn (`spawnNextOccurrence`), attachments via Convex storage, and overdue alert banners.
- **Recurring Bills & Subscriptions (`/subscriptions`):** `bills` table with cadence (`monthly`, `annual`), renewal month, category, and active status.
- **Cash Flow & Scenario Forecaster (`/forecast`):** Engine `src/finance/forecast.ts` combines cash accounts, bill schedules, and daily discretionary spend across 30/60/90-day horizons with negative-cash alerts.
- **Net Worth & Asset Tracker (`/networth`):** Groups accounts by type (including `real_estate`, `vehicle`) with snapshot history.
- **Debt Payoff Planner (`/debts`):** Supports avalanche, snowball, and minimum payment simulation (`src/finance/debts.ts`).
- **Combined Personal + Business:** Accounts support optional `scope` (`personal` default | `business`). Filtered via `ScopeFilter.tsx`.
- **Savings Goals (`/goals`):** Progress tracking from linked account balances or manual contributions.
- **Tax Center (`/tax`):** Transaction tax tags (`deductible`, `business`) and quarterly worksheet.
- **Investment Holdings (`/investments`):** Holdings table with manual price tracking without double-counting account balances.
- **Document Vault (`/documents`):** Document table + Convex storage (`convex/documents.ts`).

## Y2Y Yearly Project Planner (Built 2026-08-15)
- Located under Organize (`/y2y`, `src/pages/Y2YPage.tsx`).
- **Projects Table:** Statuses include `idea` (parking lot, no date/cost needed), `funding`, `in_progress`, `done`. Transactions gain optional `projectId` for actual spend tracking (tagged via calendar-icon dropdown).
- **Engine (`src/finance/y2y.ts`):** Calculates free cash flow profile from last ≤6 complete months (excluding transfers, current partial month, and single inflows ≥ $10k), feasibility bands (fits/tight/over), suggested target months, cut candidates, projected finish months, and bill cluster notes.
- **Features:** 2-step wizard, default 15% contingency, priority reordering (`reorderProjects`), forecast synthetic scenario integration, goal conversion (`convertGoalToProject`), and task creation from projects.

## Testing & Development Gotchas
- **Test Suites:** Comprehensive test scripts (`scripts/finance-flow-test.ts`, `scripts/pdf-import-test.ts`, `scripts/tasks-test.ts`, `scripts/expansion-test.ts`, `scripts/addons-test.ts`, `scripts/y2y-test.ts`).
- **Parallel Testing Gotcha:** E2E test suites run in parallel per invocation. `tasks-test` counts task totals and will flake if run alongside `expansion-test`'s seeded tasks; run it alone.
- **Shared Test User:** Tests share a reused test user, leading to cross-test data contamination. Month-scoped pages default to latest data; tests must explicitly navigate the `MonthPicker` to fixture months and assert on scoped locators rather than raw body text.
- **CLI Command Gotcha:** `bunx convex dev --once` fails with `EPERM: copyfile` unless run with `CONVEX_TMPDIR=./tmp` (ensure `tmp/` folder exists in project).
- **Deploys:** Platform deploys are rate-limited (20/day). Convex functions push before Vercel steps, so backend changes succeed even if frontend deployment fails.
- **UI Seeding Gotcha:** Transactions "Direction" and Bills "Type" selects persist between adds; always set them explicitly per row.
- **Port Conflict:** Kill stale `vite preview` processes on port 4173 before running Playwright tests.
