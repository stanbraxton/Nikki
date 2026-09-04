# ChurchCollar — church management

## Summary
ChurchCollar is Stan's multi-tenant church-management product for 50-500-person churches, built first for The Upper Room Assembly & Worship Center and Spring Mountain Chapel. It is a Convex + Vite + React 19 + Tailwind v4 + shadcn + Bun web app. The system provides multi-tenant operations, fund accounting, online giving, attendance tracking, and contribution statements. Project location note (from earlier notes): project directory `churchcollar-00ced88706ed`, with supporting docs previously kept in a `church-app` project folder — treat these as historical path references only, not necessarily current on Stan's own infrastructure.

## Business & pricing
- **Pricing structure:** Flat pricing model — never per-member. Draft tiers: Seed (up to 100 members) $19/mo, Grow (up to 350 members) $49/mo, Multiply (unlimited members) $89/mo [spec, 2026-08-19].
- **Data entry:** No Excel import for the core build; clean data entry only. Import may return later as a sales-onboarding feature. [Stan, 2026-08-18]
- **Branding:** No references to the previous assistant platform or any third-party assistant may appear anywhere in the product UI or metadata.
- **Tenancy:** Multi-tenant architecture from day one.

## Architecture
- **Stack:** Convex, Vite, React 19, Tailwind v4, shadcn UI, and Bun.
- **Access control:** Every church-scoped table must carry a `churchId`. Every church-scoped function must call `convex/model.ts:requireMembership()` first; write operations must also call `requireWriteRole()` to block readonly sessions.
- **Roles & memberships:** The `memberships` table maps user↔church with roles `owner`, `admin`, `finance`, and `readonly`.
- **Bootstrap:** `churches.bootstrap` — the first user seeds both initial churches and default funds as `owner`. Later workspace-gated users join existing churches with the `admin` role.
- **Money handling:** All monetary values are integer cents. Parse and display dollar amounts only via `src/lib/money.ts`.
- **Church selection:** Handled via `ChurchContext`, backed by localStorage key `churchcollar.selectedChurch`. Queries take `churchId` and skip execution when it is null.

## Data model / feature notes
- **Giving:** `batches` flow into `contributions`; contributions denormalize the `date` field to optimize statement generation. Entries can only be added or removed while a batch remains open.
- **Statements:** The `/statements` route generates household and combined letter-size PDFs, including required IRS no-goods/services language. Church letterhead fields are pulled from the `churches` table.
- **Online giving:** Public endpoint at `/give/:slug`. The access gate still blocks real congregants until access is opened for production. `onlineGiving.startGift` uses Stripe when Convex environment keys are present; without keys, it records a received test gift. No real Stripe key exists yet — Stan must provide keys before go-live. [implementation, 2026-08-19]
- **Attendance:** Modeled via `gatherings`, `attendanceSessions`, and `attendanceRecords`. A session's total attendance is the manual headcount if set, otherwise the checked-in count.
- **Fund accounting:** Fund balance = opening balance + all giving + other income − expenses + transfers in/out. See `convex/accounting.ts:fundTotals()`.

## Integrations & APIs
- **Stripe:** Used for online giving. Requires live Stripe API keys configured via Convex environment variables before go-live [2026-08-19]. No real keys are active yet; Stan must supply them.

## Status
- **Preview environment:** https://preview-bt1n6bsir1mptiqc — deployed and operational using dev Convex instance `vivid-panda-87` [deploy, 2026-08-19].
- **Production environment:** Not yet deployed. You must get Stan's explicit approval before any production deployment.
- **Access gate:** Authenticated via a `workspace_members` gate.
- **Milestones shipped in preview:** M1 (people/funds/batches/dashboard), M2 (statements), M3 (online giving), M4 (attendance), M5 (fund accounting) [deploy, 2026-08-19].
- **Test data:** The dev database contains test data generated from end-to-end (e2e) test runs [2026-08-19].

## Gotchas & lessons
- Running `bun run sync:build` requires setting `CONVEX_TMPDIR=./tmp`.
- After environment restarts, reinstall Playwright Chromium/headless shell.
- Kill stale preview servers using the bracketed pattern only: `pkill -f "vite [p]review"`. A plain `pkill -f "vite preview"` can kill its own shell process.
- If `bun add` throws an `ENOENT node_modules` error, recreate the symlink target, then run `bun install`.
- Test scripts: `scripts/test-m1-flow.ts`, `scripts/test-m2-statements.ts`, `scripts/shots.ts`. Render PDFs for visual review using `uv run --with pymupdf`.
- `bun run test` can swallow errors; debug instead by running `bunx vite preview --port 4173` in a separate process, then `APP_URL=http://localhost:4173 bun ./scripts/<test>.ts`.
- When using Playwright locators for buttons containing icons, avoid strict matches like `hasText: /^Add$/`; use substring matching such as `hasText: "Add"` instead.
- `convex/env.ts`: the Convex tsconfig lacks Node types — never reference `process.env` directly there.

## Open questions for Stan
- Should secretary logins be shared or per-church?
- Are physical envelope numbers actively in use?
- Statement delivery preference: print vs. email?
- What is the target timing for online-giving go-live?
