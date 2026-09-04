# Golden Finance — personal finance app

Golden Finance is a React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account) built for Stan Braxton. It is a comprehensive personal and business finance application that handles spending, budgets, net worth, bills, and charts, fed by automated Chase and PNC statement imports.

## Summary
Golden Finance provides automated financial ingestion, categorization, and forecasting for Stan Braxton. It includes advanced modules for debt payoff, tax tagging, asset tracking, document management, and yearly project planning (Y2Y).

## Business & pricing
- Single-user application tailored for Stan Braxton with owner authentication.
- Custom domain: `www.mygoldenfinance.com` LIVE (root 302-redirect via Squarespace forwarding; CNAME + TXT configured at Squarespace DNS) [2026-08-15].
- Production access uses public-facing routing with application-level owner sign-in protecting data.

## Architecture
- **Stack:** React, Vite, Tailwind/CSS components, Convex backend, `pdfjs-dist` for client-side PDF extraction.
- **Convex Schema (`convex/schema.ts`):** Tables for accounts, transactions, budgets, bills, netWorthSnapshots, tasks, taskUsers, scenarioItems, goals, holdings, documents, and projects (all `userId`-scoped).
- **Backend & Admin:** `convex/finance.ts` (`authenticatedQuery`/`authenticatedMutation`), `convex/adminOps.ts` (permanent secret-gated endpoints: `exportAll`, `importRows`, `upsertBills`), `convex/tasks.ts`, `convex/documents.ts`.
- **Pages & Components:** `src/pages/` (Overview/Dashboard, Transactions, Import, Budgets, Accounts, Bills, TasksPage, Subscriptions, Forecast, NetWorth, Debts, ScopeFilter, Goals, Tax Center, Investments, Document Vault, Y2YPage).
- **Testing:** Comprehensive test suite (`scripts/finance-flow-test.ts`, `scripts/pdf-import-test.ts`, `scripts/pnc-parse-test.ts`, `scripts/tasks-test.ts`, `scripts/expansion-test.ts`, `scripts/addons-test.ts`, `scripts/y2y-test.ts`). E2E suites run in parallel; test user is shared across runs.

## Data model
- **Amounts:** Negative = money out, positive = money in. Liability accounts (`credit`, `loan`) store the amount owed and subtract from net worth.
- **Transfers:** The `Transfers` category and internal transfers are excluded from income/spend totals to prevent card payment double-counting.
- **Import De-duplication:** De-dupes on `date|lowercased description|amount` (`by_user_dedupe`).
- **Extensions:** Accounts support optional `scope` (`personal` default | `business`), `apr`, and `minPayment`. Transactions support optional `taxTag` (`deductible` | `business`) and `projectId`.

## Key logic
- **Parsers & Categorization:** Keyword auto-categorizer, CSV parser, generic statement parser, and `rowsFromPncStatement` for PNC statements (handles deposits, checks, debit/online withdrawals, page-margin text, period-based year detection, and business section headers). Client-side PDF extraction via `pdfjs-dist` (reconstructs lines by y-position, matches `MM/DD desc amount`).
- **Insights & Reviews:** `buildMonthlyReview` in `src/finance/insights.ts` powers the Overview review card (where-the-money-went lines and rule-based recommendations like betting volume, subscriptions >$150, Zelle/Cash App >$500). Month pages default to the latest month with data (`useDefaultMonth`).
- **Engines:** Cash flow forecasting (`src/finance/forecast.ts`), debt payoff simulation (`src/finance/debts.ts` supporting avalanche/snowball/minimum), and yearly project planning (`src/finance/y2y.ts` calculating cash flow profile, feasibility bands, suggested target months, cut candidates, and projected finish months).

## Integrations & APIs
- **Database:** Convex Cloud database (`https://sincere-newt-681.convex.cloud`) with separate DBs for preview and production.
- **Google Drive & Backups:** Automated weekly backups (`scripts/gf_backup.py` exporting tables, uploading JSON/CSVs/README/zip to Drive folder "Personal → Golden Finance Backups") and daily statement auto-imports (`scripts/gf_statement_autoimport.py` watching Drive folder "Personal → Chase Statements").
- **Admin Operations:** Secret-gated endpoints in `convex/adminOps.ts` called via Bun (`ConvexHttpClient` + `anyApi`), using secrets from `scripts/.admin_secret` and config from `scripts/config.json`.

## Status
- Fully operational web application [2026-08-12 to 2026-08-15].
- Custom domain `www.mygoldenfinance.com` LIVE [2026-08-15].
- Permanent admin data access (`adminOps.ts`) deployed [2026-08-13].
- Recurring automations (backups, statement auto-import, monthly spending report) and 15 recurring bills seeded [2026-08-13].
- Tasks, recurring bills/subscriptions, cash flow forecasting, net worth tracking, debt payoff, multi-scope filtering, savings goals, tax center, investment holdings, document vault, and Y2Y yearly planner fully implemented [2026-08-13 to 2026-08-15].

## Gotchas & lessons
- **PNC / Business Parsers:** Unrecognized PNC section headers can silently flip transaction signs; always verify `PNC_SECTIONS` coverage. Personal and business internal transfers reference account XXXXX3465 and must remain excluded from combined totals.
- **Convex CLI:** `bunx convex dev --once` fails with `EPERM: copyfile` unless run with `CONVEX_TMPDIR=./tmp` with `tmp/` pre-created.
- **Deploys & Ports:** Platform deploys are rate-limited (20/day); Convex functions push before frontend steps, so backend changes land even if frontend deployment fails. Kill stale `vite preview` processes on port 4173 before running Playwright tests.
- **Test Contamination:** E2E tests share one reused test user, causing cross-test contamination. Month-scoped pages default to the latest month with data; tests must explicitly navigate the `MonthPicker` to fixture months and avoid asserting on raw body text (toasts). Run tests like `tasks-test.ts` alone if parallel execution causes flakes with task totals.
- **UI Seeding:** Transactions "Direction" and Bills "Type" selects persist between adds; set them explicitly per row.

## Open items
- Migrate the application from the old platform hosting to Stan's own GitHub + Firebase Hosting + Convex account [slated].
- Investigate unexplained statement items: recurring Zelle to Ihor Krislaty (~$1,300–1,800/mo, personal), "Expay Bus Csr" ($868, July, personal), and Santander billpay ($1,300, July, personal) [2026-08-13]. (Note: $50k "Instpmntout Rob Hunter" in June 2026 is confirmed intentional by Stan).
