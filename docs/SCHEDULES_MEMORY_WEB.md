# Scheduled tasks, long-term memory, and web access

## Scheduled tasks (Cloud Scheduler → Nikki)

Nikki can run a prompt on a recurring schedule with nobody in the chat.

**Create:** "Every weekday at 7am, check my Gmail for anything urgent and write a summary."
Nikki calls `schedule_task(name, cron, prompt, timezone, auto_approve)` — approval-gated, so you
confirm the name, cron and prompt before anything is created.

**Under the hood:** each schedule is a Cloud Scheduler job `nikki-sched-{name}` (region us-east4)
that POSTs to `https://nikkiaia.com/api/run` with an OIDC token from the zero-role service account
`nikki-scheduler@nikkiaia-prod.iam.gserviceaccount.com`. Nikki verifies the token (audience =
`/api/run`, email = that SA) and runs the prompt headless with the same tools as the chat.
Results land in the `scheduled_runs` table and on **https://nikkiaia.com/schedules**.

**Approval gates in unattended runs:** gated tools (`gmail_send`, `write_file`, `deploy_space`, …)
are *refused* unless the schedule was created with `auto_approve=true`. Default is read-only
behaviour — the run can research and draft, and you act on the result.

**Manage:** `list_schedules`, `schedule_runs(name)`, `run_schedule_now(name)` (fires the job
immediately — useful to test), `delete_schedule(name)` (gated).

**Manual trigger from outside** (CI, another system):
`POST https://nikkiaia.com/api/run` with header `Authorization: Bearer <ADMIN_API_TOKEN>` and body
`{"prompt": "...", "name": "optional-label", "auto_approve": false}` → `202 {"run_id", "thread_id"}`.

Cost: Cloud Scheduler's first 3 jobs per month are free, then $0.10/job/month. Each run bills the
model tokens it uses plus Cloud Run CPU while running.

## Long-term memory

Nikki keeps a small store of facts and preferences across conversations (`memories` table).

- "Remember that my Green Collar bank account is at Huntington" → `remember` (no approval).
- "What do you know about my banking?" → `recall` (full-text search).
- "Forget the thing about Huntington" → `forget` (approval-gated).

Up to 60 recent memories are injected into every system prompt, so Nikki applies them without
being asked. Keep memories short and factual; she is told to store durable facts, not chit-chat.

## Web access

- `web_search(query)` — Tavily if `TAVILY_API_KEY` is set (better ranking, ~$0.008/search after
  the free 1,000/month), otherwise DuckDuckGo with no key. Add a Tavily key with
  `printf '%s' 'tvly-…' | gcloud secrets versions add TAVILY_API_KEY --data-file=-` and redeploy.
- `http_fetch(url)` — downloads a page and returns readable text (HTML → text, PDFs → text).
  Refuses private, loopback and link-local addresses so it cannot be used to reach the Cloud Run
  metadata server or internal services.

Nikki is instructed to search whenever a question depends on current facts and to cite URLs.
