# Golden Real Estate Exam Study Guide

The Golden Real Estate Exam Study Guide is a React/Vite + Convex web app (currently hosted on the legacy hosting platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account) designed for candidates preparing for the Ohio salesperson real estate exam (PSI). It offers 1,065 original questions, timed mock exams, flashcards, and weak-area tracking. Built 2026-08-30, Stan-approved scope: free for now, salesperson track only (broker track = possible phase 2). Name chosen by Stan after a "Realtor" trademark warning — "Real Estate" is the safe generic term.

## Business & pricing
- Free for public use [2026-08-30].
- Scope restricted to the salesperson track; a broker track is a potential phase 2.
- Name selected by Stan following a Realtor trademark warning.

## Project locations
- Project name: `golden-exam-prep`.
- Preview and production URLs exist for the app (hosted on the legacy hosting platform as of 2026-08-30); production deploys still require Stan's explicit sign-off each time (standing rule) — always check with Stan before deploying to prod.

## Architecture
- Stack: React/Vite frontend + Convex backend.
- Hosting: Currently on the legacy hosting platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account. Production deployments require Stan's explicit approval each time.
- Authentication: `authenticated`, providers `["email_password"]` ONLY (no references to the previous assistant platform's sign-in per Stan's rule). `default_access=public`.
- Question bank storage: Static JSON files in `public/bank/{topic}.json`, plus `index.json` (topic counts) and `flashcards.json` — NOT stored in Convex. The client fetches and caches these files directly (`src/lib/bank.ts`). Exams pass question-id lists to Convex; grading is handled client-side (acceptable for a study tool since client trust is fine here).
- UI Theme: Warm ivory, deep navy, and gold palette featuring the Fraunces display font (`src/index.css`), with a dark navy sidebar.

## Data model
- Convex database stores only user-specific state, in these tables:
  - `questionProgress`: per user+question progress, drives mastery calculations.
  - `examSessions`: two-section state machine (national → state → done) with server-stored deadlines.
  - `flashcardProgress`: Leitner box system (boxes 1–5) with review intervals of 0, 1, 3, 7, and 21 days.
- Static data taxonomy: topic structure defined in `src/lib/topics.ts` (15 total topics; national section = 80 questions, state section = 40 questions, per the PSI outline).

## Key logic
- Readiness calculations (`src/lib/mastery.ts`): computes accuracy × coverage, weighted by official exam topic weights.
- Bank generation and processing: generated via `data/generate_bank.py` using fan-out `ai_structured_output` calls (parallel task delegation was blocked on 2026-08-30 due to a workspace resource limit on the legacy platform). The generation process is resumable, with raw batches stored in `data/raw_batches/`. Post-processing shuffles answer positions, deduplicates entries, and bans terms like "REALTOR" and "all of the above".
- Ohio-law grounding: verified against ORC 4735 and PSI CIB [2026-08-30] — 100-hour pre-license requirement per HB 238 (effective 2025-04-09), 80/40 question split between national/state sections, 70% passing threshold per section, 20-hour post-licensure requirement, 30 hours continuing education (CE) every 3 years, $250k recovery fund located in Franklin County, non-interest-bearing Ohio trust accounts. Facts are governed by `data/OHIO_FACTS.md`; question-writing rules are in `data/QUESTION_RUBRIC.md`. If the law changes, update the facts file first, then regenerate affected topics.
- Quality assurance performed: schema validation, duplicate checks, distribution verification, and manual spot-checks (all 6 sampled math answers verified by hand; flagged Ohio-related greps were confirmed to be correct distractors, not errors).

## Status
- Built and deployed [2026-08-30].
- Stan's production account (email `jaylob8@yahoo.com`, name "Stan") provisioned on prod [2026-08-30].
- All user accounts share equal privileges — there is no in-app admin role.

## Account provisioning
- Dev accounts: use `convex/adminSeed.ts`'s `createUser` (an internalAction taking email/password/name, creates pre-verified accounts).
- The `query_app_database` tool runs QUERIES ONLY — it cannot be used to provision accounts.
- For production account provisioning, use the temp-anonymous-bootstrap pattern: add a one-shot hardcoded `action` to the codebase, deploy to prod, invoke it via `ConvexHttpClient(prod_url).action(...)`, then delete the file and redeploy.
- ⚠ Critical gotcha: when creating an account profile, you must explicitly set `emailVerified: true` (this patches `authAccounts.emailVerified`). Setting only `emailVerificationTime` updates the `users` table alone and causes sign-in to silently stall into the verify flow, because the Password auth provider checks `account.emailVerified` specifically.

## Gotchas & lessons
- Convex code generation requires `CONVEX_TMPDIR=./tmp` to avoid EPERM copyfile errors across filesystems.
- End-to-end testing: run via `bun run test scripts/{study,exam}-flow-test.ts`. Requires Chromium build v1200 (`bunx playwright install chromium chromium-headless-shell`).
- Fixed bug pattern: the `submitting` flag in `ExamPage`'s `SectionRunner` must be explicitly reset on section change, because the component persists across the national→state transition.
- Exam "Exit without scoring" abandons the active session; the dashboard shows a resume banner for sessions left in progress.

## Open items
- Migrate the web app to Stan's own GitHub + Firebase Hosting + Convex account.
- Evaluate potential Phase 2 expansion to cover the broker exam track.
