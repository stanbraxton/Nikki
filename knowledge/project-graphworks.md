# GraphWorks — LLM workflow platform

GraphWorks (officially branded as **Mymation Studio** under the umbrella brand **Mymation**) is a commercial LLM workflow platform Stan wants to build and sell, positioned as a competitor to vellum.ai. Scoped in Stan's app thread on 2026-08-13; full build plan lives in the project's `todo.md`. Project id stays `graphworks`.

## Branding
- **Mymation is Stan's umbrella brand** [app thread, 2026-08-15]: this workflow platform = **Mymation Studio**; the adaptive-assistant desktop/cloud products = Mymation Assistant family (own skills: adaptive_desktop, mymation_cloud; the shared Stripe account also holds a "Mymation Cloud" product). APP_NAME "Mymation Studio" committed `e176753`; Stripe display names updated in test+live (lookup keys unchanged, still `mymation_{plan}_monthly`). One mymation.com/.ai domain plus one USPTO filing can cover both products.
- Stan chose the product name **Mymation** (my + automation) [app thread, 2026-08-14]. USPTO web screen came back clean (no MYMATION or phonetic-equivalent live marks; only a one-person animation studio "MyMation" on LinkedIn in a different industry). mymation.com/.ai/.io/.app/.net all showed no DNS [dns, 2026-08-14] — Stan must register .com + .ai himself; keep reminding him until done.
- Rejected names after trademark screens: Youmation/Umation (phonetic twins of the live "U MATION" mark, Reg 4452941, Weidmüller, automation software), Lifemation (parked .com since 2012 + "Lifomation" soundalike SaaS), 2ndU (Creatiq AI's sales agent).
- Rename committed `5909b81` (APP_NAME, billing LOOKUP_PREFIX now `mymation`, index.html title) — deployed to preview [2026-08-14]. A prior thread had briefly set APP_NAME to "Youmation"; that is superseded.
- Custom domain **www.mymation.com is LIVE** on this app [check_custom_domain, 2026-08-15] (root mymation.com 302-forwards to www via Squarespace; CNAME www→the app's hosting gateway + TXT verification records at Squarespace DNS).
- Prod access preset = **public** — Stan's rule: the custom-domain address bar must never bounce through the underlying hosting platform's own domain; the app's own login protects data. Login/signup UI rebranded — "Owner sign-in" replaces any prior third-party sign-in branding; all references to the previous hosting/engineering-assistant brand have been scrubbed (standing rule in Stan's user preferences) [deployed 2026-08-15].

## Deploy state
- First **production deploy 2026-08-14** (covered by Stan's "push all builds" approval, per app thread): reachable at a workspace-access-gated URL prior to the custom domain going live (change access via the app's space-access setting if Stan wants a given environment public). Prod Convex had NO Stripe env keys at first → billing UI showed `stripe-pending`, upgrades disabled (correct/expected until Stripe activation + a prod live-mode webhook against the prod Convex site `patient-duck-114.convex.site`).
- Deploys: preview deploys are autonomous under Stan's "Green + yellow" autonomy grant (2026-08-13); production deploys still require his explicit approval. There is a 20-deploys/24h platform cap with no queryable counter, so deploy sparingly. A scheduled job retries a pending preview deploy when the cap resets [2026-08-13].

## Locked product decisions [app thread, 2026-08-13]
- MVP wedge: visual workflow builder + deployments; custom execution engine (own IP).
- UI-first launch; Python SDK + CLI code↔UI sync planned for v2 — but the **Graph IR JSON is the canonical format from day one** (`docs/graph-ir.md` in the project) so the SDK can plug in later.
- Local git-friendly IR files + cloud execution; subscription plus bundled credits with BYO-keys supported.
- MVP additions Stan approved: minimal evals (deploy gate), templates gallery, spend guardrails, one-click rollback.
- IP guardrails: never copy Vellum's name/UI/docs/code. Functionality-only emulation is lawful (Circular 61, SAS v WPL); custom code throughout, no Dify fork (its license restricts resale).

## Project layout
- `docs/graph-ir.md` — Graph IR v1 spec (nodes/edges/config, versioning, executor contract).
- `convex/engine.ts` — pure engine helpers (topoSort, render `{{path}}`, evalCondition, primaryOutput); no Convex imports, unit-testable.
- `convex/executor.ts` — `run` action: topological execution, per-node events appended live to `executions`, conditional branch pruning via `liveIncoming`. Prompt nodes call the tool gateway `ai_structured_output` (v1 stand-in until BYO provider keys, M1).
- `convex/workflows.ts` — CRUD; **save always creates a new immutable version** and patches `workflows.latestVersionId`.
- Schema: `workflows` / `workflowVersions` / `releases` (env-scoped, M3) / `executions`.
- Editor: `src/pages/WorkflowEditorPage.tsx` (React Flow canvas + palette + inspector + Run tab), `src/components/RunPanel.tsx` (live per-node status via useQuery subscription).
- Templates live in `src/lib/templates.ts`; `workflows.create` accepts an optional starter `graph` (validated by `validateGraph`).

## Gotchas
- `bun run sync:build` needs `CONVEX_TMPDIR=./tmp` (otherwise EPERM copyfile error).
- After an environment reset: `node_modules` is a symlink into a wiped temp location — `mkdir -p` the symlink target then `bun install`; Playwright browsers are also wiped — run `bunx playwright install chromium`.
- e2e `.catch` handlers must log the error (createPageHelper failures are otherwise silent — the test runner only prints errors thrown inside the test function itself).
- Never `pkill -f "vite preview"` — the pattern matches the invoking shell itself.
- Executor ctx keys node outputs by BOTH node id and label (`{{Label.text}}`), plus `{{inputs.key}}`; code nodes receive the ctx as `vars`.
- Convex files using `process.env` need `declare const process: { env: Record<string, string | undefined> };` (frontend tsc -b lacks node types even though `convex push` accepts it).
- Internal actions referenced from an httpAction need explicit handler return-type annotations or tsc hits TS7022 circular-inference errors.
- E2E tests run on port 4189 (`scripts/test.ts`) — 4173 collides with other app previews.
- React Flow drag-connect in Playwright: click `.react-flow__controls-fitview` first (nodes can sit under the right sidebar), then use a paced mouse.move loop (~20 steps, 20ms).
- Adding a node must set `selected: true` on the RF node (onSelectionChange otherwise clears the selection immediately).
- NEVER use a Python one-liner `open(...,'w').write(...)` pattern where the value can be `None` — a failed write still truncates the file. Use proper file-write/file-edit tooling instead.

## Stripe (LIVE mode, wired 2026-08-14)
- Stan pasted his **sk_live** key (acct_1U4MHtCVztb2Jpji, Green Collar Industries). Set as `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` on the dev Convex deployment. Webhook `we_1U4NYrCVztb2JpjiHykxqv2H` → `honorable-bandicoot-809.convex.site/api/stripe/webhook` (an unsigned POST correctly returns 400). Products/prices pre-created live: `mymation_pro_monthly` $49, `mymation_scale_monthly` $199 [stripe, 2026-08-14].
- **charges_enabled: false** — Stan must finish Stripe business activation before any live checkout will succeed. The prod deploy needs its own live key + its own live-mode webhook endpoint against the prod Convex site (`patient-duck-114.convex.site`).
- Stan later pasted a **sk_test** key (2026-08-14); dev Convex env now runs TEST mode key + test webhook `we_1U4NkcCVztb2JpjilO763pkY`. Full cycle e2e-verified via `scripts/stripe-checkout.e2e.ts` (upgrade click → hosted checkout with 4242 test card → webhook grants Pro +5000 credits → API cancel → webhook downgrades to free). Gotchas: hosted checkout "Card" is a radio row, filter with `hasText:/^Card$/`; the `#email` field is hidden for returning customers (fill conditionally); the ledger row text is "Subscription started", not "grant"; cancel keeps remaining credits by design. `billing.e2e.ts` is env-agnostic on configured state. Dev workspace balance is inflated (~10k) from test grants — cosmetic only.
- **Shared Stripe account** [stripe, 2026-08-15]: the Green Collar account also serves WellCollar (5 products, own railway/convex webhooks) and Mymation Cloud. Every webhook endpoint receives ALL account events — handlers must filter: this app's handler guards `checkout`/`invoice`/`updated` events via `metadata.plan`, and `subscription.deleted` via an `onlyIfSubscriptionId` match (fixed after an audit; foreign account cancels had previously downgraded this app's customers incorrectly).

## M5 billing (2026-08-14, scaffolded — awaiting Stan's Stripe keys)
- `convex/billing.ts`: `PLANS` const (free 100 / pro $49 5k / scale $199 25k credits/mo — placeholder pricing), `billing` singleton + `creditLedger` tables, `consumeCredits`, `applySubscription`, checkout/portal actions via raw Stripe REST (no SDK; prices created idempotently by lookup_key `graphworks_{plan}_monthly` — note: this predates the later rename of the lookup prefix to `mymation`).
- 1 credit = 1 platform LLM call (prompt node without a BYO key); BYO-key calls are free. Executor tracks `rt.usage.platformCalls`, calls `rt.guardCredits()` pre-call, and `consumeCredits` after finish (both success and failure paths).
- Webhook `convex/stripeWebhook.ts` at POST `/api/stripe/webhook`: HMAC-SHA256 signature verify vs `STRIPE_WEBHOOK_SECRET`; handles `checkout.session.completed` (grant), `invoice.paid` subscription_cycle (regrant), `subscription.updated`/`deleted`. Plan travels in `metadata.plan` on the session + `subscription_data`.
- Activation needs Convex env vars set: `bunx convex env set STRIPE_SECRET_KEY sk_...` (+ `STRIPE_WEBHOOK_SECRET` from a Stripe webhook endpoint pointing at `{convex_site}/api/stripe/webhook`). UI shows a `stripe-pending` state and disables upgrades until then. e2e: `scripts/billing.e2e.ts`.

## M4 observability/evals/guardrails (2026-08-14)
- Metering lives in the executor `Runtime` (`rt.usage`); `finish` computes `costMicrocents = estTokens × 200` (blended ~$2/M tokens). Runs page: `/workflows/{id}/runs`.
- Evals: `evalCases` table, `evals.runAll` action runs the latest version per case (source "eval") and asserts contains/equals on a dot-path of the output. The deploy gate is UI-side in the promote dialog (mutations can't run graphs); an override checkbox records "evals bypassed" in the release notes.
- Spend guardrails: `workspaceSettings` singleton (`dailyRunCap`/`dailyLlmCallCap`), enforced via the `observability.guardState` internal query (scans the last 500 executions).
- BYO keys: `providerKeys` table; a prompt node calls OpenAI (gpt-4o-mini) or Anthropic (claude-3-5-haiku) directly when a key exists, else falls back to the tool gateway.
- Code node: strict-mode `new Function` with `fetch`/`globalThis`/`Function`/`crypto`/timers shadowed as undefined params. Not a real sandbox yet — WASM/worker isolation planned later.
- Remaining M4 items as of 2026-08-14: Stripe billing (needed Stan's Stripe account), USPTO name search, prod deploy approval — all since progressed (see Branding/Deploy sections above).

## M6 Composer (spec'd 2026-08-14, NOT built)
Agent-proposed workflows: a natural-language description → structured-output Graph IR proposal (with a `validateGraph` retry loop), stored as a `status: proposed` workflowVersion; the user reviews a "ghost" graph plus Q&A, and Accept promotes it and auto-creates eval cases; supports patch-based refine and a repair mode driven from execution events. Full spec in `docs/composer.md`, checklist in `todo.md`. Build after billing/launch items are done. Stan approved the spec in the app thread on 2026-08-14 — no build go-ahead has been given yet.

## M3 deployments (2026-08-14)
- Releases are append-only rows (`releases` table); rollback = re-release the most recent different version. Envs: staging/prod (dev exists in the schema but is hidden in the UI).
- Public API: `POST {convex-site-url}/api/v1/workflows/{id}/{env}/run`, Bearer key. Keys are `gwk_{env}_{48hex}`: SHA-256 hash stored in `apiKeys`, secret shown once at creation. Key generation must be an action (crypto is nondeterministic — banned in mutations).
- Deployments UI: `/workflows/{id}/deployments` (DeploymentsPage), "Deploy" button in the editor header (`open-deployments` testid). e2e: `scripts/deployments.e2e.ts` calls the real convex.site endpoint with fetch.
- Code node uses `new Function` — NOT sandboxed yet; must be isolated before exposure beyond workspace members (tracked in `todo.md` under M1).

## Open items
- Complete Stripe business activation (`charges_enabled: true`) so live customer checkouts can succeed; set up the prod live-mode webhook against `patient-duck-114.convex.site`.
- Register the `.com` and `.ai` domains for Mymation (Stan must do this himself — keep reminding him).
- Build M6 Composer (spec approved, not started) and secure sandbox isolation (WASM/worker) for code nodes (tracked in `todo.md` M1).
- USPTO filing for the Mymation name/brand (screens clean as of 2026-08-14, filing itself not confirmed done).
