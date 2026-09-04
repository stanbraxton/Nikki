# Mymation Cloud — adaptive-memory AI chat SaaS

## Summary
Mymation Cloud is Stan Braxton's proprietary transparent, consent-gated belief/memory AI chat SaaS — an auditable personal AI product with strict provenance tracking, consent-gated memory commits, and local-first execution capabilities. It is a sibling product to Mymation Desktop (repo-relative path `skills/adaptive_desktop/` in earlier notes) and is not built on Stan's previous engineering assistant's platform. Read the spec before changing, extending, or deploying it.

## Business & pricing
- **Umbrella brand (2026-08-15):** Mymation covers both product lines — this assistant family (Mymation Cloud/Desktop) AND the separate Mymation Studio workflow platform (referenced elsewhere as `graphworks`). Both share the same Stripe account; distinguish product display names with suffixes.
- **Positioning (2026-08-24):** A brief PDF and build script live at a temp working path (`mymation_brief`). Thesis: "the memory you can audit." Pillars: consent-gated memory / provenance-with-receipts / runs fully local. Marketing should lead with Desktop. Category name candidates: "auditable memory" / "consent-gated memory."
- **Competitive landscape [web, 2026-08-24]:** ChatGPT "Dreaming V3" memory went free tier in Jun 2026; Claude memory has been free since Mar 2026; Meta acquired Limitless in Dec 2025 and Rewind was sunset; Msty Aurum ($149/user/yr) is the closest paid local competitor but has no memory system.
- **Comp/admin accounts [db, 2026-08-15]:** Implemented via a subscription row with `stripe_customer_id='comp_<name>'`, status active, period_end 2099 — invisible to Stripe webhooks (portal lookup excludes `comp_%`). Stan is comped.

## Architecture
- **Repo:** Private GitHub `stanbraxton/mymation-cloud`, cloned locally to a repos working directory (`api/` = FastAPI, `web/` = Next.js). PR flow required from milestone M1 onward. Never use raw git/gh shell commands — use the `sdk.tools.github_tools.coworker_git` and `sdk.tools.coworker_github.coworker_github_cli` async tools (must await).
- **Source of truth:** Spec document (SPEC.md, v1.4) — read the relevant Part before touching anything; also a todo/status file. Secrets are stored in `.env` files (chmod 600) covering Neon, Clerk, Stripe (test sandbox), Railway, Vercel, Anthropic, OpenAI.
- **Memory design:** Retrieved episodes go into a SECOND uncached system block; the cached profile block must stay byte-identical to preserve prompt caching.

## Data model
- Migration 002 added usage cache columns. Always validate migration SQL with `pglast`.
- **Vector search:** pgvector via asyncpg — pass vectors as text literal `[x,y,...]` cast `$n::vector` (helper `memory.vector_literal`). Retrieval ceiling verified live at 0.75 cosine distance.
- **Provenance/audit:** `GET /memory/facts/{id}/history` returns provenance — source message quote, supersede chain, delta outcomes, and audit trail. The Memory page has "why?" panels and resolves pending cards.

## Key logic
- **Field-test learnings (PR #6):** The chat system prompt must FORBID the AI from claiming memory operations happened (e.g., "I've updated that" was a lie — reflection owns commits). Retold banter ("I told my friend X") is capped at ≤0.6 confidence so it never auto-commits. Differing-value candidates get superseded by the latest assertion, not reinforced.
- **M3 reflection eval:** Golden eval set at `api/tests/golden/reflection_cases.json`; live runner `api/scripts/reflection_eval.py` (needs `ANTHROPIC_API_KEY` plus dummy required env vars). Run this after ANY reflection-prompt change; bar = zero false cards. Anthropic forced tool-use (`complete_tool`) gives schema-constrained JSON; haiku initially misclassified committed moves as "update" until the prompt spelled out: update = prior stays true, contradiction = prior no longer true.
- **Nikki help widget (PR #11):** Ported from a sister product (WellCollar), help-first design — on-device keyword search over `web/src/content/helpArticles.ts` via `helpSearch.ts` (keep articles in sync with features). NOTE stemmer quirk: "stores" → "stor(e)" but "stored" stays unchanged; synonym keys must be pre-stemmed (e.g. `everyth`). Backed by Help Center at `/app/help` and API `POST /help/ask` (haiku via `complete_tool`, grounded in client-sent help excerpts, 5/min in-memory rate limit) [app, 2026-08-15].

## Integrations & APIs
- **Endpoints:** `/healthz`, `/readyz` health checks; `/admin/metrics` gated by `ADMIN_EMAILS` env var with a 10/min burst limit; `GET /memory/facts/{id}/history` (provenance); `POST /help/ask` (help queries).
- **Stripe webhook foreign-event guard (PR #9):** A shared "Green Collar" Stripe account serves Studio + Cloud + WellCollar products, so the webhook only processes events matching our price IDs / `product=mymation_cloud` metadata / already-linked subscription IDs.
- **Secrets:** Neon, Clerk, Stripe (test sandbox), Railway, Vercel, Anthropic, OpenAI credentials stored in `.env` files (chmod 600) — locations and names only, not values.

## Status
- **Live (test mode: Clerk dev + Stripe sandbox) [app, 2026-08-15]:**
  - web: `https://mymation.vercel.app` (Vercel, auto-deploys on push to main)
  - api: `https://api-production-6a94.up.railway.app` (`/healthz`, `/readyz`)
- **Milestones deployed:** M0–M4 + M5 part 1 (landing page, `/admin/metrics` gated by `ADMIN_EMAILS`, 10/min burst limit, migration 002 usage cache cols).
- **Recent PRs:**
  - PR #9: Stripe webhook foreign-event guard (see Integrations above).
  - PR #10: memory delta cards fade out ~1.5s after resolve (previously stuck in transcript forever).
  - PR #11: Nikki help widget + Help Center + `/help/ask` API (see Key logic above) [app, 2026-08-15].
- **M4:** `GET /memory/facts/{id}/history` provenance endpoint; Memory page "why?" panels and pending-card resolution — complete.
- **DNS cutover:**
  - (2026-08-15) `mymation.com` is on Squarespace DNS (same login as another Stan property, "mygoldenfinance"; verify codes are emailed to Stan and he pastes them in; the DNS modal is shadow-DOM, so use coordinate clicks). CNAME `app` → Vercel is LIVE and verified. Desktop site keeps the `www` subdomain.
  - (2026-09-03) `api` CNAME: the original Railway target (`3f12ix0s`) sat in `CERTIFICATE_STATUS_TYPE_ISSUING` for weeks despite DNS propagation and no CAA issue — fixed by deleting and recreating the custom domain via GraphQL (`customDomainCreate`, which also needs `projectId`). New required target is `4u8mx433.up.railway.app`; Squarespace CNAME still needs to be updated to point there. Check status via: `service(id){serviceInstances{edges{node{domains{customDomains{status{dnsRecords{...} certificateStatus}}}}}}}`.
  - Remaining cutover work: api SSL cert, Clerk production instance (new keys + Clerk DNS CNAMEs, needs Stan/dashboard access), Stripe live keys (Stan to provide), and env swaps (`FRONTEND_ORIGIN`, `NEXT_PUBLIC_API_URL` → `https://api.mymation.com`, Clerk/Stripe keys).

## Gotchas & lessons
- **Railway deploys:** Railway does NOT auto-deploy on push. Trigger via GraphQL at `https://backboard.railway.app/graphql/v2` with `Authorization: Bearer $RAILWAY_TOKEN` AND a User-Agent header (otherwise 403).
- Use `serviceInstanceDeployV2(serviceId, environmentId, commitSha)` — plain `serviceInstanceDeploy` redeploys the OLD commit and sometimes returns "Not Authorized". Poll `deployment(id)` for status + `meta.commitHash`; verify the deployed SHA matches `origin/main`.
- Railway env var `NIXPACKS_UV_VERSION=0.8.11` is required (works around a nixpacks empty-version pip bug).
- Railway secrets env var names are `RAILWAY_PROJECT_ID` / `RAILWAY_ENV_ID` / `RAILWAY_SERVICE_ID` (not `PROJECT_ID`) — these IDs live in Railway/Vercel secrets env files. Note: the `variables` GraphQL query returns "Not Authorized" with this token, but `variableUpsert` works fine.
- **Repo dev setup:** Before using `uv` in the repo: `export UV_PROJECT_ENVIRONMENT=.venv; unset VIRTUAL_ENV`. Backend tests: `cd api && uv run pytest`. Frontend: `cd web && npm run build`. Validate migration SQL with `pglast`. Query Anthropic model IDs via `/v1/models` since `-latest` aliases may not exist for a given key.
- **Stripe library gotcha:** In `stripe-python` 15.x, `event["data"]["object"]` is a `StripeObject`, not a dict (no `.get`) — call `.to_dict()` first. Test webhook handlers with a real `stripe.Event.construct_from(...)`, not plain dicts — that's how the M1 webhook 500 error slipped through undetected.

## Open items
- Update the Squarespace CNAME for the `api` subdomain to point to `4u8mx433.up.railway.app` and confirm the certificate issues correctly.
- Finish production cutover: API SSL cert, Clerk production instance (new keys + DNS CNAMEs — needs Stan/dashboard), Stripe live keys (needs Stan), and env swaps (`FRONTEND_ORIGIN`, `NEXT_PUBLIC_API_URL`, Clerk/Stripe keys).
- Keep help articles (`web/src/content/helpArticles.ts`) in sync with new features as they ship.
- Re-run the M3 reflection golden eval after any future reflection-prompt change to confirm zero false cards.
