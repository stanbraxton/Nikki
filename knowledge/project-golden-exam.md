# Golden Real Estate Exam Study Guide

The Golden Real Estate Exam Study Guide is a React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account) designed for candidates preparing for the Ohio salesperson real estate exam (PSI). It offers 1,065 original questions, timed mock exams, flashcards, and weak-area tracking. Built under safe generic "Real Estate" branding, it helps users efficiently master state and national exam requirements.

## Business & pricing
- Free for public use [2026-08-30].
- Scope restricted to the salesperson track; a broker track is slated as a potential phase 2.
- Name selected by Stan following a Realtor trademark warning.

## Architecture
- Stack: React/Vite frontend + Convex backend.
- Hosting: Currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account. Production deployments require Stan's explicit approval each time.
- Authentication: Email/password provider only (`authenticated`), `default_access=public`. 
- Question bank storage: Static JSON files stored in `public/bank/{topic}.json`, along with `index.json` (topic counts) and `flashcards.json`. The client fetches and caches files directly (`src/lib/bank.ts`). Exams pass question-id lists to Convex, and grading is handled client-side.
- UI Theme: Warm ivory, deep navy, and gold palette featuring the Fraunces display font (`src/index.css`) and a dark navy sidebar.

## Data model
- Convex database stores user-specific state tables:
  - `questionProgress`: Tracks progress per user and question to drive mastery calculations.
  - `examSessions`: State machine managing exams (national section → state section → done) with server-stored deadlines.
  - `flashcardProgress`: Leitner box system (boxes 1–5) with review intervals of 0, 1, 3, 7, and 21 days.
- Static data taxonomy: Topic structure defined in `src/lib/topics.ts` (15 total topics; national 80 questions, state 40 questions per PSI outline).

## Key logic
- Readiness calculations (`src/lib/mastery.ts`): Computes accuracy multiplied by coverage, weighted by official exam topic weights.
- Bank generation and processing: Generated via `data/generate_bank.py` using fan-out structured output. Resumable via raw batches stored in `data/raw_batches/`. Post-processing shuffles answer positions, deduplicates entries, and filters out terms like "REALTOR" and "all of the above".
- Ohio-law grounding: Verified against ORC 4735 and PSI CIB standards [2026-08-30] (100-hour pre-license per HB 238 effective 2025-04-09, 80/40 question split, 70% passing threshold per section, 20-hour post-licensure, 30 hours CE every 3 years, $250k recovery fund in Franklin County, non-interest-bearing Ohio trust accounts). Governed by `data/OHIO_FACTS.md` and `data/QUESTION_RUBRIC.md`. Updates require modifying the facts file first, then regenerating affected topics.
- Quality assurance: Schema validation, duplicate checks, distribution verification, and manual spot-checks (all sampled math answers and Ohio distractors manually verified).

## Status
- Built and deployed [2026-08-30].
- Stan's production account (`jaylob8@yahoo.com`, "Stan") provisioned [2026-08-30].
- All user accounts share equal privileges (no in-app admin role).

## Gotchas & lessons
- Convex code generation requires `CONVEX_TMPDIR=./tmp` to prevent EPERM copyfile errors across filesystems.
- End-to-end testing: Executed via `bun run test scripts/{study,exam}-flow-test.ts`. Requires Chromium build v1200 (`bunx playwright install chromium chromium-headless-shell`).
- Exam state bug fix: The `submitting` flag in the `ExamPage` SectionRunner must explicitly reset on section changes because the component persists across the national-to-state transition.
- Exam session abandonment: Selecting "Exit without scoring" abandons the active session; the dashboard presents a resume banner for active sessions.
- Account provisioning pattern: Development accounts use `convex/adminSeed.ts` (`createUser` internal action). Production provisioning requires the temp-anonymous-bootstrap pattern: add a one-shot hardcoded action, deploy to production, execute via `ConvexHttpClient(prod_url).action(...)`, delete the file, and redeploy.
- Auth verification requirement: `createAccount` profile execution must explicitly set `emailVerified: true` (patching `authAccounts.emailVerified`). Relying solely on `emailVerificationTime` updates only the users table, causing sign-ins to silently stall into the verification flow due to Password provider checks.

## Open items
- Migrate web app to Stan's own GitHub + Firebase Hosting + Convex account.
- Evaluate potential Phase 2 expansion for the broker exam track.
