# Nikki — architecture, infra, deploy, gotchas

Nikki is Stan Braxton's standalone private AI assistant and engineer, running on his own Google Cloud project (`nikkiaia-prod`, region `us-east4`, domain `nikkiaia.com`). This document serves as your complete operating manual, capturing architecture, deployment, engineering workflows, tools, and maintenance rules.

## Overview & Source of Truth

- **Source Repo**: `https://github.com/stanbraxton/Nikki` (private, branch `main`; local clone set up). Commit and push (`repo_commit_push`) after every shipped change.
- **Standalone Product**: Independent of any legacy infrastructure. Uses Stan's GCP account and his Anthropic/OpenAI keys. Purchases and production deployments require Stan's explicit approval.

## Technical Stack

- **API & UI (`app/main.py`, `app/ui.py`)**: FastAPI (`/healthz`, `/api/traces/{thread}`, `/api/skills` protected by bearer `ADMIN_API_TOKEN`) with Chainlit 2.12 mounted at `/`.
- **Agent (`app/agent.py`)**: LangGraph `create_react_agent`, with `interrupt_before=["tools"]` and `interrupt_after=["tools"]`. The graph is rebuilt from `registry.tools()` after every interrupt so hot-loaded tools bind correctly to the model. Default model is `anthropic:claude-sonnet-4-5`, switchable to OpenAI in settings.
- **Tools (`app/tools/`)**: Registry-managed. Approval is determined by `tool.metadata["requires_approval"]`. Built-in tools include workspace-sandboxed file operations, read-only/gated database queries (`db_schema`, `db_query`, `db_execute`), self-maintenance (`write_skill` validates via AST and trial import before hot-loading), web search/fetch with SSRF guards, memory management, Google Workspace, Microsoft Graph, custom REST APIs, scheduler, micro-spaces deployment, engineer toolchain, and knowledge base access.
- **Persistence (`app/persistence.py`)**: Cloud SQL Postgres (`nikki-pg`, user/db `nikki`) for LangGraph checkpointers, traces, memories, spaces, apps, builds, tenants, accounts, and integrations. A GCS bucket volume is mounted at `/mnt/data` for workspace files, user skill modules, and knowledge-base overlays.
- **Auth & Tenancy**: Chainlit password auth using bcrypt `ADMIN_PASSWORD_HASH` (`scripts/hash_password.py`), requiring `CHAINLIT_AUTH_SECRET`. Customer sign-up at `/signup` requires an invite code (`SIGNUP_CODE`) and defaults to `status="pending"` until approved by an admin. Every tool and persistence call is scoped by a tenant ContextVar (`app/tenancy.py`).
- **Integrations (`app/integrations/`)**: OAuth providers for Google and Microsoft, plus API-key providers for Tavily and custom REST. Managed via the `/integrations` self-service page and the `/admin` panel. OAuth tokens are Fernet-encrypted at rest (`app/crypto.py`).

## Infrastructure & Deployment

- **Infrastructure Scripts**: `scripts/infra.sh` provisions APIs, runtime service accounts, Artifact Registry repositories, GCS bucket volumes, empty secrets, Cloud SQL `db-f1-micro` (`nikki-pg`), and Cloud DNS zones. `scripts/deploy.sh` drives Cloud Build to Cloud Run, mounts GCS at `/mnt/data`, attaches secrets, and configures domain mapping. The gcloud CLI operates from `/tools/google-cloud-sdk/bin/gcloud`.
- **Cloud Run Service**: Live at `https://nikkiaia.com` (Google-managed certificate, issued 20:47 UTC; takes ~70 min after DNS resolution) and `https://nikki-895240122598.us-east4.run.app`.
- **Domain & DNS**: `nikkiaia.com` maps to Cloud Run with `CertificateProvisioned` and `DomainRoutable True`. DNS is hosted at Squarespace with standard A and AAAA records.
- **Deploy Command**: 
  ```bash
  PROJECT=nikkiaia-prod REGION=us-east4 DOMAIN=nikkiaia.com bash scripts/deploy.sh
  ```
  *(Note: `deploy.sh` builds the working tree, not HEAD. Always check `git status` and stash or commit stray files before deploying.)*
- **Self-deploy (you can ship your own changes)**: after `repo_commit_push` on `stanbraxton/Nikki`, call `deploy_self` (gated — Stan approves in chat). It snapshots the pushed HEAD, runs `cloudbuild.yaml` on Cloud Build (docker build → Artifact Registry → `gcloud run deploy nikki --image …`, which keeps the service's secrets/env/volumes) and rolls a new revision in ~5 minutes. Follow with `build_log(<id>)`; the build id starts with `nikki-`. Expect a brief blip when the new revision takes traffic. `scripts/deploy_now.sh` is the same path for a human with gcloud. Refuse to deploy with uncommitted or unpushed changes.

## Gotchas & Troubleshooting

- **Health Probes**: The Google front end returns its own 404 for `/healthz` on `*.run.app` and `nikkiaia.com`, and `/healthz/` falls through to Chainlit HTML. Probe production health using `POST /api/run` (which returns 401 without auth) or an authenticated `GET /api/schedules`.
- **Secret Manager**: Running `printf '' | gcloud secrets versions add` creates no version and causes Cloud Run deployments to fail. Optional secrets are seeded with a single newline (`"\n"`), and application code treats whitespace-only secret values as unset.
- **gcloud Configuration**: gcloud configuration is located at `CLOUDSDK_CONFIG=/tools/.gcloud`. Always export `CLOUDSDK_CONFIG` and update `PATH` when executing manual gcloud commands.
- **Process Killing**: Never use `pkill -f` or `pgrep -f` with a pattern matching your own command line, as it will kill the calling shell (exit status 143). Always terminate background processes by PID from log files.
- **Beta Components**: Running `gcloud beta ...` prompts for interactive component installation and hangs non-interactive shells. Run `gcloud components install beta -q` beforehand.
- **Domain Verification**: The Site Verification API requires a separate OAuth scope; user credentials receive 403 errors, so domain verification must be completed by Stan in a browser.

## Core Engines & Capabilities

### Spaces Engine (v2)
- Deploy micro-apps to Cloud Run via `app/tools/spaces.py` (`deploy_space`, `space_status`, `list_spaces`, `delete_space`). Asynchronous execution returns "queued" while gcloud runs in a thread, updating the `spaces` Postgres table.
- Accessible via the `/spaces` gallery (`app/spaces_gallery.py`) and monitored live in the chat UI via `watch_space` status cards.
- Services are named `nikki-space-{slug}`, deployed in `us-east4` under zero-role service account `nikki-spaces@`, labeled `managed-by=nikki`.

### Web, Google Workspace, Scheduler & Memory (v3)
- **Web (`app/tools/web.py`)**: `web_search` uses Tavily if configured or falls back to DuckDuckGo; `http_fetch` blocks private, loopback, and link-local IP addresses.
- **Memory (`app/tools/memory.py`)**: Managed via the `memories` table. Remember and recall operations are ungated; forget is gated. A digest of up to 60 recent memories is injected into your system prompt.
- **Scheduler (`app/scheduler.py`, `app/headless.py`)**: `POST /api/run` triggers background tasks logged to `scheduled_runs`. Cloud Scheduler jobs (`nikki-sched-{name}`) invoke `/api/run` using OIDC authentication from `nikki-scheduler@`.

### Multi-Tenant SaaS & Engineer Toolchain (v4 / v5)
- **Tenancy**: Scoped via `app/tenancy.py`. Admin tenant is `"admin"`. Tenant self-service signup requires approval via the admin dashboard (`/admin`).
- **Engineer Toolchain (`app/tools/engineer.py`)**: Allows you to manage GitHub repositories, run lint/type checks, and deploy applications (`deploy_app`) by snapshotting committed HEAD to Cloud Build, running dependency installation, optional Convex deployment, and Firebase Hosting deployment (`firebase-tools@14 deploy --only hosting`).
- **Knowledge Base (`app/tools/knowledge.py`)**: Manages Markdown documents in the repository `knowledge/` directory and overlay storage at `/mnt/data/knowledge`. The index is automatically injected into your system prompt.

## UI Customization & Brand Assets

- **Changing Your Own UI**: You CAN edit your own interface. The chat UI is built with Chainlit, configured via `.chainlit/config.toml` (`[[UI.header_links]]`), `public/nikki.js` (which injects admin header links), and `public/nikki.css`. Other pages (`/admin`, `/spaces`, `/integrations`, `/schedules`, `/signup`) are static HTML files in `app/`. Workflow: edit, commit, and push changes to `stanbraxton/Nikki`, then inform Stan that a redeployment of the Cloud Run service is required. Never state that a UI change is impossible.
- **Brand Assets**: Asset generation script `scripts/brand_assets.py` (using PIL and Lato-Bold) maintains avatars, favicons, app touch icons, and header lockups using the Nicole cartoon avatar source asset. Custom HTML pages include proper favicon links and avatar headings.

## Maintenance, History Repair & Knowledge Base Sync

- **Repository Commits**: Always fetch and rebase on `origin/main` before pushing (`repo_git`, then `repo_commit_push`); other maintainers also commit to the repo.
- **History Repair Hook**: To prevent Anthropic 400 errors caused by interleaved turns or interrupted tool approvals, `app/agent.py` includes `repair_history()` and a `_pre_model_hook` that strips orphan tool results and synthesizes error messages. Concurrent messages are handled via per-thread asyncio locks in `app/ui.py`.
- **History Trimming**: `trim_history()` in the same hook drops the oldest turns (cutting only at user-message boundaries) so each request stays under `NIKKI_HISTORY_BUDGET_TOKENS` (default 24,000). The stored thread is untouched; only what is sent to the model is trimmed. Long-term memory and the knowledge base carry context across threads.
- **Prompt Caching** (token cost): on Anthropic the system prompt is split into a stable half (persona, capabilities, KB index, rules — marked `cache_control: ephemeral`) and a volatile half (user, clock, memory digest) that follows it, and the last message of every request carries a cache breakpoint, so the whole conversation prefix is served from cache on the next turn (5-minute TTL, ~10% of normal input price). Keep new always-on instructions in the stable half.
- **Tool Output Compaction**: tool results from earlier turns are shortened to `NIKKI_OLD_TOOL_RESULT_CHARS` (default 1,500) before being re-sent; the current turn's results stay complete and the stored thread is untouched. `kb_read` returns 8,000 chars by default (page with `offset`, raise `max_chars` up to 60,000), `http_fetch` 6,000. Prefer `kb_search` for a specific fact. Cheapest habit for the user: one thread per topic.
- **Model Fallback**: When the primary model (`NIKKI_MODEL`, Anthropic claude-sonnet-4-5) refuses a call for billing reasons (account out of credit), the turn is retried automatically on `NIKKI_FALLBACK_MODEL` (default `openai:gpt-4.1-mini`) with a one-line notice. The fix for the primary is on Stan's Anthropic account: console.anthropic.com → Plans & Billing (enable auto-reload); no redeploy needed.
- **Rate Limits**: Stan's OpenAI organization has a 30,000 tokens-per-minute cap on gpt-4.1; requests above that fail with a 429 "Request too large". Raising limits: platform.openai.com/account/rate-limits.
- **Error Etiquette** (Stan's standing rule): on any error, never show only the raw message — state the problem, list possible solutions, and give a recommendation.
- **Knowledge Base Sync**: You hold all project knowledge as well as private records (health, financial, legal, and family categories). Business and project documents reside in the repository `knowledge/` directory and overlay; private documents are stored exclusively in the secure overlay (`gs://nikkiaia-prod-nikki-data/knowledge/`), prefixed with '_Private — for Stan only_'. You must never disclose private records outside direct conversations with Stan. Use `skills/nikki/scripts/kb_sync.py` to synchronize knowledge base assets.
