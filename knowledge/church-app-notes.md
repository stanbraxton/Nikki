# ChurchCollar — Realm feature map and import notes

## Overview and Realm Feature Map
As you work on ChurchCollar, keep in mind the full module inventory of Realm (ACS Technologies) mapped to ChurchCollar milestones, as parsed on [2026-08-19]:

- **M1 (Current)**: Member profiles & households (people, families, contact info, photos, custom fields), contributions entry (cash/check gifts, batch posting by fund), funds/designations (tithe, building, missions, tax-deductible flags), and multi-campus admin (Multiply feature: multi-tenant from first commit supporting Upper Room and Spring Mountain).
- **M1 (Thin)**: Dashboards / analytics (giving and engagement insight, YTD by fund, counts, recent batches).
- **M2**: Contribution statements (one-year tax statements, IRS-compliant PDFs).
- **M3**: Online giving (Vanco-equivalent: card/ACH, recurring, pledges via Stripe Connect + platform fee) and pledge campaigns.
- **M4**: Attendance & check-in (worship, class attendance, kids check-in).
- **M5**: Groups & events (small groups, registrations, comms) and fund accounting (GL, AP, budgets, payroll via integration). Plus communication (email/text blasts in M5+).
- **Skipped / Enterprise Niche**: Discipleship pathways and volunteer scheduling (since Planning Center handles serving rosters for free).
- **M6**: Member app/portal (self-service giving, directory after M3 online giving).

### Deliberate Differences (The Wedge)
- **Flat pricing**: $19 / $49 / $89 by congregation size with unlimited users, compared to Realm's higher monthly full-stack pricing.
- **Set-up-in-an-afternoon**: Designed to avoid Realm's weeks of paid onboarding.
- **Treasurer-first reporting**: Addresses Realm's primary review complaint of unusable reports by ensuring every ChurchCollar report answers one secretary or treasurer question plainly.
- **Modern fast UI**: Solves Realm's most-cited UI/UX weaknesses.

## Secretary's Excel Format and Import Notes
You should note the structure of historical finance files discovered in Google Drive (Team's Account) under the Church/Accounting folder (`id: 0BzvmZC63s41VdWw5cHdMMVZMMkk`) [gdrive, 2026-08-18], with local copies in the local downloads directory:
- **Files observed**: `2017 The Upper Room Finances.xls` (old format, mostly empty sheets), `2019 THE UPPER ROOM FINANCES.xlsx`, `2021 THE UPPER ROOM FINANCES.xlsx`.
- **Workbook structure (2019+ format)**: One workbook per year. One tab per household/giver (tab name = family name in uppercase letters, e.g., `CAPITALIZED FAMILY NAME`).
- **Household tab columns**: `DATE | TITHE | [FOOD PANTRY] | [EXTRA/GIVING/GIFTS GIVEN] | [notes]`. Column sets vary per tab and headers have typos (`TITHE`, `TITHE GIVEN`, `FOOD PANTR`, `MONEY PAID OUR`); apply fuzzy matching.
- **Individual attribution**: Inside a household via the note column (e.g., rows tagged with specific family member initials or names).
- **Totals rows**: Date = None with a number indicates a running total; skip these on import.
- **Special tabs**: `EXTRA GIVING` tab for one-off/non-member givers (`DATE | WHOM | amount | WHERE & WHAT FOR`), `MONEY IN & OUT` tab for weekly offering totals, expenses, and an `ATTENDANCE` column (they do track attendance!), `MISSIONS` and `FOOD PANTRY` tabs for fund-level in/out ledgers with payee notes, and `<YEAR> FINAL` tab for annual rollups (income by fund, expenses by category like supplies, insurance, taxes, land payments, music, funerals, repairs).
- **Junk tabs**: Ignore tabs named `BLANK`, `BLANK (3)`, `Sheet2`, `Sheet3`, and `~$` lock files in Drive.

### Import Implications & Scope Rules
- **Scope Rule [stan, 2026-08-18]**: **NO Excel import.** Build from scratch with clean data entry.
- Funds exist implicitly in historical files (Tithe, Food Pantry, Missions, and Extra/designated); seed these.
- Historical workbooks track expenses and attendance, providing real historical context for M4 and M5.
- Contributions can attach to a Person (note-tagged) or Household (default).

### Open Data Gaps [2026-08-18]
- The `2021` workbook actually contains `2019` data (copied as a template and never updated), meaning the newest real data in Drive is from 2019. Files for subsequent years are likely on the secretary's computer (Stan was asked about this).
- No Spring Mountain Chapel finance file was found anywhere in Drive.

## Build Status and Milestone Roadmap
You must follow the build status and architectural rules outlined in the application skill documentation.

### M1 Core — DONE [2026-08-19]
- **Schema**: Built for churches, memberships, households, people, funds, batches, and contributions.
- **Bootstrap Seeds**: Seeded Upper Room, Spring Mountain, and 5 default funds.
- **UI**: Includes church switcher, Dashboard, People, Giving (batch entry), and Funds pages.
- **Testing**: End-to-end test passing (`scripts/test-m1-flow.ts`).
- **Deployment**: Preview deployed to the legacy preview environment.
- **Approval**: Stan approved M1 [stan, 2026-08-19].
- **Rule**: Production deployment requires explicit approval from Stan.

### M2 Statements — DONE [2026-08-19]
- **Statements Page (`/statements`)**: Features a year picker, per-household PDF generation, and combined "Download all" PDF generation.
- **Compliance**: Includes IRS declarations, deductible funds only, per-fund subtotals, and envelope numbers.
- **Customization**: Church letterhead (address, ZIP, EIN) is editable in-app.
- **Testing**: End-to-End test verifies real PDF downloads (`scripts/test-m2-statements.ts`).
- **Open Item**: Email delivery of statements is pending Stan's decision on print vs. email.

### M3 Online Giving — DONE [2026-08-19]
- **Public Giving Page**: Accessible at `/give/:slug` (fund selection, amount presets, name/email) requiring no account.
- **Stripe Integration**: Uses Stripe Checkout and signed webhooks when `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are set, with a test-mode fallback otherwise.
- **Admin Giving Page (`/online`)**: Includes giving link, review queue, email-to-household matching, and posting to online giving batches (method: "online").
- **Testing**: End-to-end test script (`scripts/test-m3-online-giving.ts`) covers give → review → post → statement total.
- **Pending Items**: Real Stripe keys needed from Stan before handling live money; public access on the giving page requires workspace membership gating before congregants can use it; per-church Stripe Connect is required to sell to other churches.

### M4 Attendance — DONE [2026-08-19]
- **Schema**: Includes `gatherings`, `attendanceSessions`, and `attendanceRecords` tables.
- **Attendance Management (`/attendance`)**: Take-attendance form, gatherings management (recent-8 average, active toggle), and recent sessions list.
- **Session View (`/attendance/:sessionId`)**: Household-grouped check-in roster with search, manual headcount (which takes precedence over checked-in counts for totals), and session deletion.
- **Rules**: Exactly one session per gathering and date (resumes existing session instead of duplicating).
- **Testing**: End-to-end test script (`scripts/test-m4-attendance.ts`).
- **Nice-to-Have**: Dashboard attendance trend tile.

### M5 Fund Accounting — DONE [2026-08-19]
- **Schema**: Includes `categories` and `transactions` tables (handling income, expense, transfer, and opening types with positive integer cents).
- **Backend (`convex/accounting.ts`)**: Overview (all-time balances including auto-giving), transaction listing (fund filter, limit 100), report (date-range per-fund and category breakdown), category CRUD, and create/delete transaction with validation.
- **Accounting Page (`/accounting`)**: Balances table, record-transaction form (dynamic fields per type, inline new category creation), income & expense report, and register with deletion support.
- **Testing**: End-to-end test script (`scripts/test-m5-accounting.ts`) passed, verifying opening balances, expense categorization, fund transfers, and resulting ledger balances.
- **Future / Nice-to-Have**: Report PDF export for board meetings and bank reconciliation (phase 2).

### Future Roadmap
Follow the sequence: M4 attendance → M5 fund accounting → M6 go-to-market, referring to the Realm parity map as needed.
