# Nikki — architecture, operations and roadmap

Nikki is Stan Braxton's standalone AI assistant and engineer, running on his own Google Cloud project. It is being
built into a sellable multi-tenant SaaS under the standalone "Nikki" brand. This document is Nikki's own
operating manual.

## Stack
- **UI**: Chainlit 2.x mounted at `/` inside a FastAPI app (`app/main.py`, `app/ui.py`). Real-time token streaming,
  expandable reasoning/tool steps, approval gates (Approve/Reject) before gated tools run.
- **Agent**: LangGraph ReAct agent (`app/agent.py`), default model `anthropic:claude-sonnet-4-5`, switchable to
  OpenAI in settings. Graph is rebuilt from the tool registry after every interrupt so hot-loaded skills bind.
- **Tools** (`app/tools/`): files (workspace-sandboxed), db_schema/db_query/db_execute, self-maintenance
  (`write_skill` — AST + import checks + trial import, then hot-load), web (search + fetch with SSRF guard), memory
  (remember/recall/forget), Google Workspace (Drive, Calendar; Gmail admin only), Microsoft 365, custom REST APIs,
  scheduler, Spaces (single-file micro-apps on Cloud Run), **engineer** (git repos → Cloud Build → Firebase Hosting +
  Convex) and **knowledge base** (this folder).
- **Persistence**: Cloud SQL Postgres (`nikki-pg`, db/user `nikki`) — LangGraph checkpoints, Chainlit threads, traces,
  memories, spaces, apps/builds, tenants/accounts/integrations. GCS bucket mounted at `/mnt/data` for workspace files,
  user skills and the knowledge-base overlay.
- **Auth**: own email/username + password (bcrypt). Admin tenant `admin` (user `stan`). Customer sign-up at `/signup`
  is gated by an invite code (`SIGNUP_CODE`). Every tool call is scoped by a tenant ContextVar (`app/tenancy.py`).
- **Integrations** (`app/integrations/`): OAuth providers google, microsoft; api-key providers tavily, custom_rest.
  Tenant page `/integrations`; admin page `/admin` (provider client credentials, tenant list). Tokens are
  Fernet-encrypted at rest.
- **Scheduler**: Cloud Scheduler jobs `nikki-sched-*` call `POST /api/run` (OIDC from `nikki-scheduler@`), results at
  `/schedules`.

## Cloud footprint (project `nikkiaia-prod`, region us-east4)
- Cloud Run service `nikki` (1 vCPU / 1 GiB, min 1 instance, session affinity), runtime SA `nikki-runtime@`.
- Domain https://nikkiaia.com (Google-managed cert; DNS at Squarespace). Fallback https://nikki-895240122598.us-east4.run.app.
  The Google front end 404s `/healthz` — probe with `POST /api/run` (expects 401) or a logged-in `GET /api/me`.
- Secrets (Secret Manager): ANTHROPIC_API_KEY, OPENAI_API_KEY, ADMIN_PASSWORD_HASH, CHAINLIT_AUTH_SECRET,
  ADMIN_API_TOKEN, DATABASE_URL, GOOGLE_OAUTH_CLIENT_ID/SECRET, TAVILY_API_KEY (placeholder), GITHUB_TOKEN
  (placeholder until Stan adds a token), `nikki-app-{slug}-convex` per registered app.
- Google OAuth consent screen: In production, External, 100-user lifetime cap for unverified sensitive scopes → brand
  verification needed before ~100 customers connect Google. [2026-09-04]
- Deploy: `PROJECT=nikkiaia-prod REGION=us-east4 DOMAIN=nikkiaia.com bash scripts/deploy.sh` (Cloud Build → Cloud Run).
  Infra bootstrap in `scripts/infra.sh`. Source of truth: GitHub `stanbraxton/Nikki` (private).

## Engineer workflow (how Nikki maintains software)
1. `repo_open owner/name` → read with `repo_list`/`repo_read`/`repo_search`.
2. Edit with `repo_edit` (exact snippet) or `repo_write`; check `repo_git diff`; run small checks with `repo_run`
   (typecheck/lint/unit tests only — no full builds, the container has 1 GiB).
3. `repo_commit_push` (gated) with a clear message.
4. `deploy_app slug` (gated): snapshots committed HEAD → Cloud Build (bun install → `convex deploy --cmd 'bun run build'`
   → `firebase deploy --only hosting`). Follow with `app_status`; don't poll in a loop.
5. Apps are registered once with `register_app` (repo, Firebase site, optional Convex production deploy key which is
   stored in Secret Manager). Custom domains via `add_custom_domain` (returns DNS records for Stan).
Working trees live in `/tmp/repos` and may vanish when the instance recycles — `repo_open` re-clones; nothing is
lost because GitHub is the source of truth. Never deploy with uncommitted changes.

## Roadmap
- **Phase B (SaaS)**: safe tenant tools, Stripe billing (never per-seat), usage metering, admin dashboard.
- **Phase C**: landing page, onboarding, Google brand verification (+ CASA if Gmail goes public), Spaces for tenants.
- **Migration**: move Stan's 11 web apps and 5 static pages from the old platform to GitHub + Firebase Hosting +
  his own Convex account, then maintain them through the engineer toolchain (see project-* documents).

## Lessons
- Secret Manager refuses empty payloads; optional secrets hold a single newline and code treats whitespace as unset.
- Cloud Run `gcloud run deploy --source` from a service account needs more IAM than documented (bucket-scoped
  storage.admin on `run-sources-*`, a custom bucket-list role, serviceAccountUser on the compute default SA).
- Managed certificates can take 60+ minutes after DNS is correct; don't loop-poll.
- Stan's rule: no references to the previous assistant platform anywhere in his software.
