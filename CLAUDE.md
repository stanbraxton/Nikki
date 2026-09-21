# Nikki — Engineering Handoff

Nikki is Stan Braxton's standalone private AI assistant at **https://nikkiaia.com** — a Chainlit + FastAPI + LangGraph app running in Stan's own Google Cloud project. It is self-contained: it depends on no other assistant platform, and no third-party assistant product should be named in Nikki's UI, docs, or knowledge base. Any purchase or production deploy needs Stan's explicit go-ahead first.

This document is the accumulated operating knowledge for working on Nikki's codebase safely. Read it fully before making changes — most of it exists because something broke in exactly the way it warns about.

> **Currency:** verified against the code on 2026-09-21. Two sections had drifted from the implementation and are corrected below (§2 approval mechanism, §8 index size); both are marked. When a section here disagrees with the code, the code wins — and fix the section.

---

## 1. Source control & local workflow

- **Repo:** `https://github.com/stanbraxton/Nikki` (private), branch `main`.
- **Push:** `git push https://x-access-token:$GITHUB_TOKEN@github.com/stanbraxton/Nikki.git HEAD:main`. Commit as `Stan Braxton <stan@greencollarindustries.com>`. Plain `git push` with a normal credential helper often fails in an agent sandbox — use the token form. (On Stan's own Mac, an SSH key works fine and is simpler.)
- **Before editing:** `git fetch` first — Nikki (the app) sometimes commits to her own repo via her self-maintenance tools, so check for new commits before you start.
- **Stray `knowledge/*.md` diffs:** local knowledge files sometimes show uncommitted diffs that aren't yours — `git checkout -- knowledge/` before committing, then re-apply any edits you actually intended. If you *do* mean to commit knowledge changes, check the diff is additions only before staging.
- **Deleting files in a sandbox:** in some agent environments `rm` is denied inside the working folder, and git then leaves `.git/index.lock` and `tmp_obj_*` behind after a commit. A stale `index.lock` blocks the next git command. Clean with `find .git \( -name '*.lock' -o -name 'tmp_obj_*' \) -delete` once deletion is permitted.
- **Local test env:** no full local server run (importing `app.ui` hangs outside a real server) — import/test modules individually, under a `timeout`. Env vars needed: `NIKKI_WORKSPACE_DIR`, `NIKKI_SKILLS_DIR`, `DATABASE_URL=sqlite:///...`, `SPACES_PROJECT=nikkiaia-prod`, `SPACES_REGION=us-east4`, `GCLOUD_BIN=<path to gcloud>`, `CLOUDSDK_CONFIG=<gcloud config dir>`. The venv has no pip preinstalled — use `uv pip install --python .venv/bin/python <pkg>`.
- **Python version:** the repo targets **3.12** (`.python-version`, `python:3.12-slim`). `app/tools/documents.py` uses a backslash inside an f-string expression, legal only from 3.12 — it will fail `py_compile` on 3.10/3.11. That is a sandbox artefact, not a bug.
- **Testing a function without the full app:** most of `app/` imports chainlit / langchain / sqlalchemy. To exercise one function in a bare environment, pull its source out with `ast.get_source_segment` and `exec` it with stubs. This tests the real shipped text rather than a paraphrase of it.

## 2. Stack overview

- `app/main.py` — FastAPI + Chainlit app. Key HTTP endpoints: `/api/run`, `/api/schedules`, `/api/skills`, `/api/me`.
- `app/agent.py` — LangGraph ReAct agent: model selection/fallback/routing, approval interrupts, conversation history repair/trim/compaction, the system prompt.
- `app/ui.py` — Chainlit layer: streaming, tool steps, the approval gate, per-turn budget.
- `app/guards.py` — per-turn limits and the pre-push Convex static check.
- Postgres in production (Cloud SQL), SQLite for local dev.
- Multi-tenant with admin approvals. Admin-only tool families: files, db, self-maintain, spaces, engineer, browser, speech, sports, images, jobs.

### Approval UI — do not regress this  *(corrected 2026-09-21)*

**Never use Chainlit's `cl.AskActionMessage` / `cl.AskUserMessage`.** They're bound to the websocket's `call` ack; any reconnect (phone sleep, a new Cloud Run revision, the idle timeout, a second browser tab) fires `clear_ask` and the approval buttons silently vanish while the turn hangs waiting for an answer that can never arrive. That warning stands.

**The prescribed replacement in the previous version of this document is also now wrong.** It described an `asyncio.Future` held in `_pending_approvals[thread_id]`, with a 15-minute timeout. The code deliberately moved away from that, and `ask_approval`'s own docstring says why: *"no Chainlit message handler waits for a decision: awaiting one leaves Chainlit's Stop control active and disables the composer."*

What the code actually does:

- `_pending_approvals` is a `set[str]` of thread ids — an in-process hint only, never the source of truth.
- The real record is durable, in the `approvals` table. `persistence.create_pending_approval(thread_id, tenant_id, email, tool_names, ttl=timedelta(hours=5))` writes one row per paused checkpoint. A partial unique index makes close/reopen atomic across instances; a superseded pending approval is retired rather than deleted, preserving the audit trail.
- **The TTL is 5 hours**, not 15 minutes (commit `064740e`). `expire_pending_approvals()` marks lapsed rows `expired` without touching the graph checkpoint.
- Nothing awaits. `ask_approval` renders a `cl.Message` with approve/reject `cl.Action` buttons and **returns**, leaving the composer usable. A later button click or typed reply starts a fresh Chainlit turn that resumes the checkpoint.
- `claim_pending_approval()` is the single gate, and returns one of: `claimed`, `no_pending`, `expired`, `not_owner`, `approved_running`, `rejected_running`, `completed`, `superseded`, `failed`. Each has a distinct user-facing message. Treat it as a state machine — don't add a path that bypasses it.
- `approval_intent()` parses a typed "approve"/"yes"/"go ahead" or "reject"/"no" from an ordinary chat message, so a reload or a mobile client without working buttons can still answer.
- `_action_target()` rejects an action whose payload doesn't belong to the current thread, before it reaches the graph.

Resuming a rejection goes through `rejection_messages(calls, reason)` + `graph.aupdate_state(..., as_node="tools")`. **Reuse that path** for any new "refuse these tool calls" case — it keeps the checkpoint valid and lets the model see a normal tool error it can reason about.

### Tool error contract

Tool functions must **return errors as text**, never raise. A raised exception inside a tool kills the entire turn instead of letting the model see the error and recover/report it. (This bit Nikki early: hand-written SQL with wrong-case column names raised and killed the run — fixed by making `db_query` return `"query error: ..."` instead of raising.)

## 3. Production infrastructure

- GCP project: `nikkiaia-prod`. Cloud Run service `nikki`, region `us-east4`, 2Gi memory. Cloud SQL instance `nikki-pg`. Custom domain `nikkiaia.com` (DNS is on Squarespace, which has **no API** — DNS changes require Stan to paste records manually).
- **Deploy pipeline:** any push to `main` triggers Cloud Build trigger `nikki-main-deploy`. **The trigger runs in `us-east5`; the Cloud Run service is in `us-east4`** — easy to check the wrong region and conclude a build never ran.

  ```bash
  gcloud builds list --region us-east5 --project nikkiaia-prod --limit 3
  gcloud run services describe nikki --region us-east4 --project nikkiaia-prod \
    --format 'value(status.latestReadyRevisionName)'
  ```

- **Rollback**, worth knowing *before* you need it — a Nikki broken at import cannot redeploy herself, so `deploy_self` is useless in exactly the case you'd want it:

  ```bash
  gcloud run services update-traffic nikki --to-revisions=<previous>=100 --region us-east4
  ```

- Prefer push-to-deploy only. There's also a manual `scripts/deploy.sh` / `deploy_now.sh` / a `deploy_self` engineer tool — using either of those *as well as* pushing to main double-deploys; pick one path per change.
- gcloud CLI calls from an agent sandbox routinely take 25–35 seconds — always run them in the background/detached and poll for completion rather than using a short synchronous timeout.

## 4. Model configuration, fallback, and turn budget

- `app/config.py`: `NIKKI_MODEL` defaults to `anthropic:claude-sonnet-4-5` (primary). `NIKKI_FALLBACK_MODEL` defaults to `openai:gpt-4.1-mini`.
- `app/agent.py`: `is_billing_error(e)` detects provider refusals ("credit balance is too low", "insufficient_quota", "billing_hard_limit_reached", "exceeded your current quota"); `fallback_model_for()` picks the fallback spec; on a billing error, `app/ui.py`'s `_run_turn` → `_drive()` rebuilds the LangGraph graph on the fallback model and resumes the turn. Same logic in `app/headless.py` for scheduled/headless runs.
- **The fallback only helps if the fallback provider still has credit.** This caused the 9/19–9/20 outage: Anthropic failed on a billing error, it fell back to OpenAI, and OpenAI *also* had none (`429 insufficient_quota`), so every turn failed with no working model left. A billing issue, not a code bug — top up Anthropic (console.anthropic.com → Plans & Billing, enable auto-reload) and/or OpenAI. No redeploy needed; Nikki works the instant the balance is positive.
- **A billing fallback into a small model mid-engineering-turn is its own hazard** — `gpt-4.1-mini` will cheerfully keep editing and pushing. Consider `anthropic:claude-haiku-4-5` as the fallback, or no fallback on engineering turns so they fail loudly.
- `friendly_error(e)` renders a "Problem / Possible solutions / Recommendation" message on unrecoverable failures — deliberate house style, keep it when adding new error paths.
- History budget: `NIKKI_HISTORY_BUDGET_TOKENS` (24000, sized for the OpenAI fallback) / `NIKKI_HISTORY_BUDGET_TOKENS_ANTHROPIC` (80000). Stan's OpenAI org has a 30,000 TPM cap on some models, so long threads can hit `429 Request too large` even with credit. `_pre_model_hook` = `mark_cache_breakpoint(compact_old_tool_results(trim_history(repair_history(...))))` — all non-destructive; the stored checkpoint is never modified, only what is sent to the model.
- **Per-turn budget** (`app/guards.py`, `NIKKI_*` in config): `max_tool_rounds` (16), `max_repeated_tool_calls` (2), `turn_token_ceiling` (400k), `recursion_limit` (18 — was 40, higher than LangGraph's own default of 25). The budget is keyed to the **thread**, not the `TurnRenderer`: an approval resumes the turn in a fresh Chainlit turn with a new renderer, and a renderer-scoped budget would reset at every approval. Since the loop it guards against was made of *gated* calls, that scoping is the whole point — don't "simplify" it onto the renderer.
- **Token accounting:** `TurnRenderer.note_usage()` dedupes on message id. `astream` runs with `stream_mode=["messages", "updates"]`, so each model call is seen twice — once as chunks, once as the completed `AIMessage`. Both carry `usage_metadata`. Assigning loses all but the last round; naively adding doubles everything.

## 5. Skills (Nikki-authored tools)

- Production skills directory `/mnt/data/skills` mirrors `gs://nikkiaia-prod-nikki-data/skills/*.py` and hot-reloads on file mtime — uploading a `.py` there is the fastest way to ship a new skill without a full deploy.
- `app/tools/self_maintain.py`: `_validate()` resolves `app.*` imports via `importlib.util.find_spec` and rejects hardcoded secrets or placeholder return values; `test_skill(source, tool, args)` runs the candidate in a subprocess; `write_skill(name, source, test_call)` **requires a passing test call** before it will save.
- Gotcha already hit once: a Nikki-authored skill imported `app.browser` (wrong — the real module is `app.tools.browser`) and called a `@tool`-wrapped function directly instead of via `.func(...)`; the trial import only exercises module-level code so this slipped through the validator. Fixed by making the validator resolve imports properly; still, always `read_skill` before trusting a skill Nikki calls "live".

## 6. Background jobs & website automation

- Preferred method order for any web-automation admin task: `browser_*` tools first → `browser_network` / `browser_network_detail` / `browser_cookies` to capture the underlying request → `http_request` directly once the request shape is known → `job_start` only for something that needs to run long/unattended.
- `app/tools/jobs.py` creates a Cloud Run Job (`nikki-job-<name>`) running `python:3.12-slim` with the script passed via a base64-encoded env var; secrets are injected from Secret Manager via `secrets=`, never via a plain env var whose name matches `PASS`/`SECRET`/`TOKEN`/`API_KEY` (those are refused outright as an env value).
- Job logs land in Cloud Logging as `jsonPayload`, not `textPayload` — filter accordingly. `job_status` can incorrectly say "no job" for ~30s right after `job_start` because the execution isn't listed yet.
- Browser tools cannot get through Cloudflare/MFA-gated portals (e.g. va.gov, ID.me) from a GCP IP by design — the fallback pattern is Stan downloads the file himself and uploads it to chat for `read_document` to process.

## 7. Micro-Spaces / "engineer" tooling

- `app/tools/spaces.py` + `app/tools/engineer.py` let Nikki build and deploy standalone apps: GitHub repo + Convex backend (deploy key from Stan's Convex dashboard, stored as a Secret Manager secret) + Firebase Hosting, all inside `nikkiaia-prod`.
- Starter template: `stanbraxton/nikki-app-template` — React/Vite/Tailwind/shadcn, Convex Auth (password + Resend email codes), orgs/memberships/invites. `scaffold_app(slug, title, description, firebase_site)` clones it and provisions secrets; `deploy_app` pushes the Convex env then deploys.
- Known pitfalls: setting the JWKS secret via a Python `repr()` string instead of proper JSON breaks auth-provider discovery; deploying Firebase Hosting without `--spa` causes 404s on client-side routes; Convex build fails in Cloud Build unless `convex/_generated/` is committed.
- **`repo_check` before any push.** Read-only, ungated, instant. A real `tsc` is not available in the 1 GiB container (`repo_run` forbids `bun install`, and tsc can't resolve imports without `node_modules`), so `app/guards.py::convex_lint` checks statically for the two patterns that have broken every Convex build here:
  - an argument declared `v.optional(...)` compared inside a `.withIndex()` callback against a column that is **required** in `schema.ts` — TypeScript discards narrowing across the callback boundary;
  - `let q = ctx.db.query(...)` reassigned from `.withIndex(...)` — `QueryInitializer` vs `Query`, TS2739.
  It reads the repo's own `schema.ts`, and must: when the indexed column is *itself* optional, `.eq()` accepts undefined and there is no error. Three earlier versions of this check lacked that and would each have blocked correct code. `repo_commit_push` refuses a staged `convex/` change that trips it; `force=True` overrides and should be justified out loud.

## 8. Knowledge base

- Nikki's knowledge lives in the repo's `knowledge/` directory plus a Cloud Storage overlay at `/mnt/data/knowledge`; anything private goes only in the gitignored `knowledge-private/`. The repo copy ships in the image and is the source of truth.
- `knowledge/nikki-system.md` is Nikki's own self-description to herself — update it whenever a new tool family is added, or she won't know the capability exists.
- ~~Known gap: `index_text(max_chars=1400)` truncates the prompt index.~~ **Corrected 2026-09-21:** already raised to `4000`. The repo index currently renders at ~1,650 chars, so there is headroom.
- **The system prompt routes; the knowledge base explains.** As of 2026-09-21 the admin prompt no longer carries per-domain playbooks — it carries a routing table telling Nikki to `kb_read` the relevant doc before acting. When you add a tool family, add its rules to the matching KB doc and add one routing line, rather than growing the prompt. It went 1,123 → 347 words on every admin turn; keep it that way.

## 9. House style / product conventions

- Error messages to the user follow a strict "⚠️ Problem / Possible solutions / Recommendation" structure — see `friendly_error()` in `app/agent.py`.
- No third-party assistant platform should be named anywhere a user or Nikki's own knowledge base can see. Nikki is standalone.
- Any change touching production (a deploy, a purchase, an infra change) needs Stan's explicit sign-off before it happens — don't auto-execute those just because a fix is obvious.
- Credentials live in the gitignored `.secrets/` (not present in a fresh clone). Never read, echo, or commit them; never paste one into a chat transcript.

## 10. Working method — earned the hard way

Nearly every mistake made on this codebase looked correct while it was being made. The pattern:

- **Verify against the real file, not against a memory of it.** A reading of `main` is not a reading of the feature branch. A pattern that compiles in one file may fail in another because the *schema* differs, not the code.
- **Negative tests carry the weight.** A guard that flags a real bug but also flags correct code will be switched off within a week. Test the things that must stay silent at least as hard as the things that must fire.
- **A fix that doesn't clear its own gate isn't a fix.** Run the proposed correction through the check that flagged the original.
- **Check the scope of any per-turn state against the approval flow.** Anything keyed to a `TurnRenderer` resets at every approval gate, and the expensive failures are made of gated calls.

---

*Last verified against the code: 2026-09-21.*
