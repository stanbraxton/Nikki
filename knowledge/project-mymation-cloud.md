# Mymation Cloud — adaptive-memory AI chat SaaS

## Summary
Mymation Cloud is a transparent, consent-gated belief and memory AI chat SaaS designed for users seeking auditable personal AI interactions. It features strict provenance tracking, consent-gated memory commits, and local-first execution capabilities, operating as a sibling to Mymation Desktop under the broader Mymation umbrella.

## Business & pricing
- **Umbrella Brand:** Mymation covers both the assistant family and the separate Mymation Studio workflow platform. Both share a single Stripe account; product display names are distinguished with suffixes.
- **Positioning [2026-08-24]:** Built around the thesis "the memory you can audit." Core pillars include consent-gated memory, provenance-with-receipts, and local execution. Marketing leads with Desktop. Category name candidates: "auditable memory" or "consent-gated memory."
- **Competitive Landscape [2026-08-24]:** ChatGPT introduced "Dreaming V3" memory free tier [2026-06]; Claude memory has been free since [2026-03]; Meta acquired Limitless [2025-12] and Rewind sunsetted. Msty Aurum ($149/user/yr) is the closest paid local competitor (though it lacks a memory system).
- **Comp Accounts:** Admin and comped accounts utilize a subscription row with `stripe_customer_id='comp_<name>'`, active status, and period end set to 2099, making them invisible to Stripe webhooks (portal lookups exclude `comp_%`). Stan is comped [2026-08-15].

## Architecture
- **App Stack:** A React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account). 
- **Repository:** Private GitHub repository `stanbraxton/mymation-cloud` (`api/` FastAPI backend, `web/` Next.js frontend). Pull request workflow required from M1 onward.
- **Infrastructure & Services:** Uses Neon (PostgreSQL with pgvector), Clerk, Stripe (test sandbox), Railway, Vercel, Anthropic, and OpenAI.
- **Memory Storage:** Retrieved episodes reside in a second uncached system block to ensure the primary cached profile block remains byte-identical for prompt caching.

## Data model
- **Schema & Migrations:** Migration 002 includes usage cache columns. Migration SQL must be validated using `pglast`.
- **Vector Search:** Utilizes `pgvector` via `asyncpg`. Vectors are passed as text literals `[x,y,...]` and cast via `$n::vector` (using the `memory.vector_literal` helper). Retrieval ceiling is verified live at 0.75 cosine distance.
- **Provenance & Audit:** Supports deep traceability (`GET /memory/facts/{id}/history`), exposing source message quotes, supersede chains, delta outcomes, and audit trails.

## Key logic
- **Memory & Chat Prompting:** The chat system prompt strictly forbids the AI from claiming memory operations have occurred (reflection owns commits). Casual retold banter ("I told my friend X") is capped at ≤0.6 confidence to prevent auto-committing. Differing-value candidates are superseded by the latest assertion rather than reinforced.
- **M3 Reflection & Evaluation:** Evaluated using a golden set at `api/tests/golden/reflection_cases.json` via the live runner `api/scripts/reflection_eval.py` (requires `ANTHROPIC_API_KEY` and dummy environment variables). Must be run after any reflection prompt change, targeting zero false cards. Anthropic forced tool-use (`complete_tool`) enforces schema-constrained JSON, distinguishing between updates (prior stays true) and contradictions (prior no longer true).
- **Nikki Help Widget:** Provides on-device keyword search over `web/src/content/helpArticles.ts` via `helpSearch.ts` (accounts for stemmer quirks, e.g., "stores"→"stor(e)" while "stored" remains unchanged; synonym keys must be pre-stemmed such as `everyth`), backed by the Help Center at `/app/help` and the API endpoint `POST /help/ask` (haiku via `complete_tool`, grounded in client-sent help excerpts with a 5/min in-memory rate limit) [2026-08-15].

## Integrations & APIs
- **Endpoints & Limits:** Includes health endpoints (`/healthz`, `/readyz`), an admin metrics route (`/admin/metrics` gated by `ADMIN_EMAILS` with a 10/min burst limit), provenance history (`GET /memory/facts/{id}/history`), and help queries (`POST /help/ask`) [2026-08-15].
- **Stripe Webhooks:** Features a foreign-event guard (since a shared Green Collar account serves Studio, Cloud, and WellCollar) ensuring webhooks only process events matching our price IDs, `product=mymation_cloud` metadata, or already-linked subscription IDs.
- **Credentials & Secrets:** Secure environment secrets (chmod 600) configured for Neon, Clerk, Stripe, Railway, Vercel, Anthropic, and OpenAI.

## Status
- **Live Deployments [2026-08-15]:** Web is live at `https://mymation.vercel.app` (Vercel, auto-deploys on push to main); API is hosted at `https://api-production-6a94.up.railway.app`.
- **Milestones:** M0 through M4, plus M5 part 1 deployed (landing page, admin metrics, usage cache columns). 
- **Recent PRs:** PR #9 (Stripe webhook foreign-event guard), PR #10 (memory delta cards fade out ~1.5s after resolve), and PR #11 (Nikki help widget and `/help/ask` API) [2026-08-15]. M4 fully implements memory provenance history and resolution panels.
- **DNS Cutover [2026-08-15 / 2026-09-03]:** `mymation.com` is managed on Squarespace DNS. CNAME `app` points to Vercel (LIVE). API custom domain recreated via GraphQL with target `4u8mx433.up.railway.app` (Squarespace CNAME update pending).

## Gotchas & lessons
- **Railway Deployments:** Railway does *not* auto-deploy on push. Deployments must be triggered via GraphQL (`https://backboard.railway.app/graphql/v2`) using `Authorization: Bearer $RAILWAY_TOKEN` and a required User-Agent header (otherwise 403). Use `serviceInstanceDeployV2(serviceId, environmentId, commitSha)` because plain `serviceInstanceDeploy` redeploys old commits and can return "Not Authorized". Poll `deployment(id)` to verify the deployed SHA matches `origin/main`.
- **Railway Configuration:** Requires `NIXPACKS_UV_VERSION=0.8.11` to prevent the Nixpacks empty-version pip bug. Project, environment, and service IDs live in secrets files (`RAILWAY_PROJECT_ID`, `RAILWAY_ENV_ID`, `RAILWAY_SERVICE_ID`). Note that the `variables` GraphQL query returns "Not Authorized" with tokens, but `variableUpsert` succeeds.
- **Python & Stripe:** In `stripe-python` 15.x, `event["data"]["object"]` is a `StripeObject` (not a plain dict and lacks `.get`)—always call `.to_dict()` first. Test webhook handlers using `stripe.Event.construct_from(...)` rather than plain dictionaries to avoid silent failures.
- **Development Environment:** Prior to using `uv`, run `export UV_PROJECT_ENVIRONMENT=.venv; unset VIRTUAL_ENV`. Backend tests are run via `cd api && uv run pytest`, and frontend builds via `cd web && npm run build`. Anthropic model IDs must be queried via `/v1/models` as `-latest` aliases may not exist.

## Open items
- Complete remaining DNS cutover steps (update Squarespace CNAME for the API domain to `4u8mx433.up.railway.app`).
- Finalize production cutover items: API SSL certificate, Clerk production instance (new keys and Clerk DNS CNAMEs), Stripe live keys, and environment variable updates (`FRONTEND_ORIGIN`, `NEXT_PUBLIC_API_URL` pointing to `https://api.mymation.com`).
- Execute the migration of app infrastructure from the old platform host to Stan's own GitHub + Firebase Hosting + Convex account.
