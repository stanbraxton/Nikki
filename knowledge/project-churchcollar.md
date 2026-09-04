# ChurchCollar — church management SaaS

## Summary
ChurchCollar is a dedicated church-management product tailored for 50-500-person churches, initially built for The Upper Room Assembly & Worship Center and Spring Mountain Chapel. It is architected as a React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account). The system provides multi-tenant operations, fund accounting, online giving, attendance tracking, and contribution statements.

## Business & pricing
- **Pricing structure:** Flat pricing model—never per-member. Draft tiers: Seed (up to 100 members) at $19/mo, Grow (up to 350 members) at $49/mo, and Multiply (unlimited members) at $89/mo [2026-08-19].
- **Data entry:** No Excel import for the core build; clean data entry only. Import functionality may be reintroduced later during sales onboarding [2026-08-18].
- **Branding:** No platform or third-party assistant references are permitted anywhere in the product UI or metadata.
- **Tenancy:** Multi-tenant architecture from day one.

## Architecture
- **Stack:** Convex, Vite, React 19, Tailwind v4, shadcn UI, and Bun.
- **Access Control:** Multi-tenant design requires every church-scoped table to carry a `churchId`. Every church-scoped function must invoke `convex/model.ts:requireMembership()` first; write operations must execute `requireWriteRole()` to block read-only sessions.
- **Roles & Memberships:** The `memberships` table maps users to churches with specific roles: `owner`, `admin`, `finance`, and `readonly`.
- **Bootstrap:** `churches.bootstrap` allows the first user to seed both initial churches and default funds as an `owner`. Subsequent workspace-gated users join existing churches with an `admin` role.
- **Financial Representation:** All monetary values are handled as integer cents. Parsing and display of dollar amounts must use `src/lib/money.ts`.
- **Church Selection:** Handled via `ChurchContext` backed by the localStorage key `churchcollar.selectedChurch`. Queries take `churchId` and safely skip execution when null.

## Data model
- **Giving:** Structured via `batches` flowing into `contributions`. Contributions denormalize the `date` field to optimize statement generation. Entries can only be added or removed while a batch remains open.
- **Attendance:** Modeled through `gatherings`, `attendanceSessions`, and `attendanceRecords`. A session's total attendance evaluates to the manual headcount if explicitly set, defaulting otherwise to the checked-in count.
- **Fund Accounting:** Fund balance is calculated as: opening balance + all giving + other income − expenses + transfers in/out (see `convex/accounting.ts:fundTotals()`).

## Key logic
- **Statements:** The `/statements` route generates household and combined letter-size PDFs complete with required IRS no-goods/services language. Church letterhead fields are pulled directly from the `churches` table.
- **Online Giving:** Exposes a public endpoint at `/give/:slug`. The `onlineGiving.startGift` function utilizes Stripe when Convex environment keys are present; in their absence, it records a received test gift.

## Integrations & APIs
- **Stripe:** Used for online giving processing. Requires live Stripe API keys configured via Convex environment variables before go-live [2026-08-19]. No real keys are active yet; Stan must supply them.

## Status
- **Preview Environment:** Deployed and operational using dev Convex instance `vivid-panda-87` [2026-08-19]. Shipped milestones include M1 (people, funds, batches, dashboard), M2 (statements), M3 (online giving), M4 (attendance), and M5 (fund accounting) [2026-08-19].
- **Production Environment:** Not yet deployed. Stan requires explicit authorization before executing any production deployment.
- **Access Gate:** Authenticated via a `workspace_members` gate. The space gate currently blocks real congregants until access is officially opened for production.
- **Test Data:** The development database contains test data generated from end-to-end test runs [2026-08-19].

## Gotchas & lessons
- **Build Settings:** Running `bun run sync:build` requires setting `CONVEX_TMPDIR=./tmp`.
- **Environment Restarts:** After sandbox environment restarts, Playwright Chromium/headless shell must be reinstalled.
- **Process Management:** Stale preview servers must be terminated using the bracketed pattern only: `pkill -f "vite [p]review"`. A plain `pkill -f "vite preview"` can inadvertently terminate its own shell process.
- **Package Management:** If `bun add` throws an `ENOENT node_modules` error, recreate the symlink target and then run `bun install`.
- **Testing & QA:**
  - Core test scripts include `scripts/test-m1-flow.ts`, `scripts/test-m2-statements.ts`, and `scripts/shots.ts`.
  - Render PDFs for visual inspection using `uv run --with pymupdf`.
  - `bun run test` can swallow errors; debug instead by running `bunx vite preview --port 4173` in a separate process, then executing `APP_URL=http://localhost:4173 bun ./scripts/<test>.ts`.
  - When using Playwright locators for buttons containing icons, avoid strict matches like `hasText: /^Add$/`; instead, use substring matching with `hasText: "Add"`.
- **Convex TypeScript Config:** `convex/env.ts` resides where the Convex tsconfig lacks Node types; never reference `process.env` directly.

## Open items
- Determine secretary login architecture (shared logins vs. per-church accounts).
- Clarify whether physical envelope numbers are actively in use.
- Confirm statement delivery preferences (print vs. email).
- Finalize online-giving go-live timing and schedule.
