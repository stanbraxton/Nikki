# Building new apps (playbook)

How Nikki turns "build me an app for X" into a live, multi-tenant web app on Stan's own
infrastructure (GitHub → Cloud Build → Convex + Firebase Hosting). Read this before any
new-app request. Micro-Spaces (`deploy_space`, Cloud Run) are for small single-purpose tools
without accounts; everything with users and data goes through this playbook.

## The stack (every app, no exceptions)

- Frontend: React 19 + Vite + TypeScript + Tailwind 4 + shadcn/ui, Biome for lint/format.
- Backend: Convex (database, queries/mutations/actions, HTTP routes, crons). One Convex
  project per app, **production** deployment only.
- Auth: Convex Auth, email + password with 6-digit verification and reset codes via Resend.
- Hosting: Firebase Hosting site in the `nikkiaia-prod` GCP project (`https://<site>.web.app`),
  custom domain later via `add_custom_domain`.
- Source of truth: private GitHub repo under Stan's account. The working tree is scratch.

## Phase 0 — clarify (one short message, not a questionnaire)

Confirm: app name + slug, who the users are (single company vs. many customer orgs), the 3–5
core objects (e.g. shops, inspections, invoices), and anything that must integrate (Stripe,
external APIs). Pricing is never per-seat. If Stan says "use your judgment", proceed.

## Phase 1 — scaffold (minutes)

1. `scaffold_app(slug, title, description)` — gated. Creates `stanbraxton/<slug>` from the
   starter template, templates the name, pushes the first commit, creates the Firebase site,
   generates Convex Auth keys and stages the Convex env (JWT_PRIVATE_KEY, JWKS, SITE_URL,
   ADMIN_SECRET, RESEND_API_KEY) in Secret Manager, registers the app.
2. Ask Stan for **one thing**: a Convex production deploy key. Exact words: "Convex dashboard →
   New project, name it `<slug>` → open the Production deployment → Settings → Deploy Keys →
   Generate → paste it here." Nothing else is needed from him.
3. `register_app(slug, title, "stanbraxton/<slug>", convex_deploy_key=<key>)` — stores the key
   in Secret Manager. Then `deploy_app(slug)`. First deploy = template live at the site URL.
   Tell Stan the URL and that the real build starts now.

## Phase 2 — build the real app

`repo_open` the repo and read `README.md` first — it maps the template. Then:

- **Schema** (`convex/schema.ts`): replace the example `items` table. Every tenant table has
  `orgId: v.id("orgs")` and at least one index that starts with `orgId`. Add `createdAt`/`updatedAt`.
- **Functions**: one file per domain in `convex/`. Use `orgQuery`/`orgMutation` from
  `convex/functions.ts` (gives `ctx.orgId`, `ctx.userId`, `ctx.membership`). Never accept an
  `orgId` argument from the client; verify `doc.orgId === ctx.orgId` before patch/delete.
  `requireAdmin(ctx)` for destructive or billing actions. Cron/HTTP work uses `internal*` builders.
- **Pages**: `src/pages/<Name>Page.tsx`, export from `src/pages/index.ts`, route inside the
  `AppLayout` block in `AuthenticatedAppRoutes.tsx`, entry in `navItems` in `AppSidebar.tsx`.
  Structure: page header → stat cards → main content. Empty states explain what to do first.
- **Landing** (`src/pages/LandingPage.tsx`): real positioning, features, pricing (flat or
  usage-based, never per-seat), CTA → `/signup`.
- **Brand**: oklch tokens at the top of `src/index.css` (light + `.dark`), fonts in
  `index.html`, favicon/apple-touch icon in `public/`. Contrast ≥ 4.5:1.
- **Email**: `sendResendEmail()` in `convex/email.ts`. Sender is `<App> <no-reply@wellcollar.com>`
  until the app has its own verified Resend domain; `EMAIL_FROM` env overrides.
- **Secrets / API keys**: never in code. `set_convex_env(slug, NAME, value)` stages them; they
  are applied by the next `deploy_app`. `list_convex_env(slug)` shows names only.
- **Errors shown to users**: Problem → Possible solutions → Recommendation.
- Commit in coherent chunks with clear messages; `repo_commit_push` then `deploy_app` after each
  milestone. Report the URL and what changed; don't wait for perfection.

## Phase 3 — verify

- `app_status(slug)` / `build_log(id)` until the build is `success`. Typical failures: type
  errors (run `repo_run(repo, "bun run typecheck")` before deploying — never `bun install` or a
  full build inside your own container), missing Convex env var, stale `convex/_generated`.
- Open the live URL with the browser tools: sign up with a fresh address, verify the code flow
  works (Resend), create the org, exercise the main pages, sign out/in.
- `GET https://<deployment>.convex.site/health` → `{ok:true}`;
  `/admin/stats` with `x-admin-secret` for counts.

## Gotchas (learned on real deploys)

- **Commit `convex/_generated/`.** Cloud Build runs `tsc -b` before codegen; stale or missing
  generated types fail the build. After the first successful deploy the generated files are
  current — commit them.
- Convex deploy keys are per deployment and start with `prod:`; a `dev:` key deploys to the
  wrong place. Custom domains on Convex need the Pro plan — we don't use them.
- `bunx convex logs` streams forever; use `--history` or the repo's `scripts/logs.ts`.
- Firebase Hosting site ids are global — if `<slug>` is taken, pass `firebase_site="<slug>-app"`.
- Custom domains: fully active = `hostState HOST_ACTIVE` and **no** `certState`. Firebase's
  resolver caches old DNS for the record TTL; deleting/recreating the domain does not help — wait.
  Squarespace DNS refuses apex A changes until its own 4×198.x A records and domain forwarding are
  removed; `www` should be a CNAME to `<site>.web.app`.
- Resend: only verified domains can send; a send-only API key cannot add domains.
- Convex Auth password flow: sign-up → verification code email → verified; reset uses the same
  code UI. If a user says "no email", check RESEND_API_KEY is staged and the deploy ran after it.
- Anything that leaves the box (push, repo creation, deploy, env, domains) is approval-gated —
  batch the asks so Stan approves once per milestone, not once per file.
- Never mention or reference any previous hosting platform or engineering assistant in app
  code, copy, commit messages, or docs.

## Reference: existing apps built this way

WellCollar (oil & gas back office, live at www.wellcollar.com) is the fullest example of the
pattern — multi-tenant companies, roles, Resend emails, public API with `x-api-key`, admin HTTP
with `x-admin-secret`, crons. RedCollar, ChurchCollar, GraphWorks, Golden Finance/Picks/Market/
Exam, PlainLaw, WaffleHouse follow the same structure (see their project docs).

## Micro-Spaces (deploy_space) — operating rules

_Moved verbatim out of Nikki's system prompt on 2026-09-21. It used to load on every
turn regardless of topic; it now loads when the work is actually in this area._

Spaces: you can build and deploy independent web micro-apps (dashboards, trackers, calculators) with deploy_space. Write a complete, self-contained, production-quality app: for 'fastapi' pass a full index.html (inline CSS/JS, responsive, polished) and optionally a main.py exposing `app` for JSON endpoints; for 'streamlit' pass a full app.py; for 'node' pass index.html plus an optional Express server.js. Pick a short slug, state the slug and framework before calling the tool, and tell the user the build takes 3-6 minutes and the link appears in the Spaces Gallery (/spaces). Do not poll space_status repeatedly in one turn; the UI tracks progress.

## New apps from the starter template — operating rules

_Moved verbatim out of Nikki's system prompt on 2026-09-21. It used to load on every
turn regardless of topic; it now loads when the work is actually in this area._

NEW APPS: when asked to build a new web app (SaaS, tracker, portal, tool with users/data), do NOT start from an empty repo — call scaffold_app(slug, title, description) (gated). It creates the GitHub repo from the starter template (React/Vite/Tailwind + Convex Auth email/password + multi-tenant orgs/teams/invites + example CRUD + admin HTTP + Firebase Hosting), prepares the Convex env (auth keys, SITE_URL, ADMIN_SECRET, RESEND_API_KEY) and registers the app. Then ask the owner for ONE thing: a Convex production deploy key (dashboard → new project named after the slug → Production → Settings → Deploy Keys). Store it with register_app(..., convex_deploy_key=...), add app-specific API keys with set_convex_env, deploy_app, and then build the real domain on top: read the repo README.md first; replace the example `items` table; keep every tenant table keyed by orgId and use orgQuery/orgMutation; add pages + sidebar entries; write real landing copy (never per-seat pricing). Deploy again after each meaningful milestone and report the URL. Micro-Spaces (deploy_space) are for small single-purpose tools without accounts; scaffold_app is for real apps with users and data.
