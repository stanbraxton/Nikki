# GraphWorks — LLM workflow platform

GraphWorks (officially branded as **Mymation Studio** under the umbrella brand **Mymation**) is a commercial LLM workflow platform designed as a competitive alternative to Vellum.ai `[2026-08-15]`. Built for developers and technical operators, it provides a visual workflow builder, custom execution engine, versioned deployments, spend guardrails, and built-in evaluations.

## Business & pricing
- **Umbrella Brand**: Mymation (`[2026-08-14]`), covering Mymation Studio (the workflow platform) and the Mymation Assistant family (desktop/cloud products).
- **Pricing Tiers**: Placeholder pricing model with bundled monthly credits (1 credit = 1 platform LLM call without BYO key):
  - Free: 100 credits/mo
  - Pro (`mymation_pro_monthly`): $49/mo, 5,000 credits (`[2026-08-14]`)
  - Scale (`mymation_scale_monthly`): $199/mo, 25,000 credits (`[2026-08-14]`)
- **Monetization & Credit System**: Subscriptions include bundled credits; users can also provide Bring-Your-Own (BYO) API keys for zero platform credit consumption. Raw Stripe REST actions handle checkouts and portals.
- **IP & Legal Guardrails**: Custom codebase throughout (no Dify fork due to resale restrictions). Strictly functionality-only emulation of competitor tools, avoiding copied names, UI, docs, or code (per Circular 61 / SAS v WPL).

## Architecture
- **Tech Stack**: React/Vite + Convex web app (currently hosted on the old platform; slated for migration to Stan's own GitHub + Firebase Hosting + Convex account).
- **Frontend**: React Flow canvas, palette, inspector, and Run tab (`src/pages/WorkflowEditorPage.tsx`, `src/components/RunPanel.tsx`).
- **Backend & Engine**: Convex-powered backend with pure engine helpers (`convex/engine.ts`), topological execution runner (`convex/executor.ts`), workflow CRUD (`convex/workflows.ts`), and billing/webhook handlers (`convex/billing.ts`, `convex/stripeWebhook.ts`).
- **Custom IP Engine**: Standalone execution engine supporting topological sort, template rendering (`{{path}}`), conditional branch evaluation, and primary outputs.

## Data model
- Core Convex tables and schemas:
  - `workflows`: Container records; saving always creates a new immutable version and updates `workflows.latestVersionId`.
  - `workflowVersions`: Immutable definitions of graphs (`docs/graph-ir.md`), including proposed drafts (`status: proposed` for Composer).
  - `releases`: Environment-scoped append-only release records (staging/prod/dev).
  - `executions`: Live append-only event logs per run.
  - `evalCases` & `evals`: Test cases for workflow deployments and assertion tracking.
  - `workspaceSettings`: Singleton for daily run and LLM call caps.
  - `providerKeys`: Storage for BYO provider API keys.
  - `apiKeys`: Hashed public API keys (`gwk_{env}_{48hex}`) for external execution triggers.
  - `billing` & `creditLedger`: Subscription singletons and credit transaction history.

## Key logic
- **Execution & Graph IR**: Graph IR JSON is the canonical format from day one (`docs/graph-ir.md`). The `run` action executes nodes topologically, appending live events to `executions` and pruning conditional branches via `liveIncoming`.
- **Context & Variable Resolution**: Executor keys node outputs by both node ID and node label (`{{Label.text}}`), alongside `{{inputs.key}}`. Code nodes receive execution context via `vars`.
- **Metering & Guardrails**: Runtime usage tracks platform LLM calls (`rt.usage.platformCalls`), guarded by `rt.guardCredits()` pre-call and `consumeCredits` post-call (covering success and failure paths). Cost is estimated at `costMicrocents = estTokens × 200` (blended ~$2/M tokens). Spend is restricted by workspace daily limits enforced via `observability.guardState`.
- **Code Nodes**: Implemented using strict-mode `new Function` with `fetch`, `globalThis`, `Function`, `crypto`, and timers shadowed as undefined (temporary stand-in pending WASM/worker isolation).
- **Evaluations & Deploys**: `evals.runAll` executes the latest version against test cases, asserting output paths. Deploy gates require passing evals in the UI promote dialog unless explicitly bypassed with audit logging.
- **Composer (M6 - Spec'd)**: Specification defined for agent-proposed workflows (`docs/composer.md`) converting natural language descriptions into Graph IR proposals via structured output and validation retry loops (`[2026-08-14]`).

## Integrations & APIs
- **Stripe Integration**: Wired for LIVE and TEST modes (`acct_1U4MHtCVztb2Jpji`, Green Collar Industries). Webhook endpoint at `/api/stripe/webhook` verifies HMAC-SHA256 signatures (`STRIPE_WEBHOOK_SECRET`) and processes `checkout.session.completed`, `invoice.paid`, and subscription updates/deletions. Shared Stripe account events are filtered using metadata `plan` and subscription ID matching.
- **Public API**: External execution endpoint `POST {convex-site-url}/api/v1/workflows/{id}/{env}/run` authenticated via Bearer API keys (`gwk_{env}_{48hex}`). Key generation is implemented as an action due to cryptographic nondeterminism.

## Status
- **Branding & Domains**: Umbrella brand Mymation established `[2026-08-15]`. Custom domain `www.mymation.com` is live with public access preset and rebranded login UI (`[2026-08-15]`).
- **Deployments**: First production deploy completed `[2026-08-14]`. Staging and production releases fully supported.
- **Modules Built**: M3 deployments, M4 observability/evals/guardrails, and M5 billing scaffolded (`[2026-08-14]`). M6 Composer and code node isolation remain unbuilt specifications.

## Gotchas & lessons
- **Convex Build & Environment**: `bun run sync:build` requires `CONVEX_TMPDIR=./tmp` to prevent EPERM copyfile errors. Convex files using `process.env` require explicit `declare const process` typings. Internal actions referenced by HTTP actions need explicit return-type annotations to prevent TS7022 circular-inference errors.
- **Testing & E2E**: Playwright tests run on port 4189 to avoid preview port collisions. React Flow drag-and-drop in tests requires clicking fit-view first and pacing mouse movements. Newly added nodes must explicitly set `selected: true` to prevent immediate deselection.
- **Stripe & Webhooks**: Hosted checkout "Card" selection requires precise radio filtering (`hasText:/^Card$/`), and email fields must be handled conditionally for returning customers. Shared Stripe accounts require strict webhook metadata filtering to prevent foreign cancellations from affecting platform subscriptions.

## Open items
- Complete Stripe business activation (`charges_enabled: true`) to enable live customer checkouts.
- Register official `.com` and `.ai` domains for Mymation.
- Migrate the application repository and hosting from the old platform to Stan's own GitHub + Firebase Hosting + Convex account.
- Implement M6 Composer and secure sandbox isolation (WASM/worker) for code nodes.
