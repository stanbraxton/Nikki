# Nikki Spaces — build & deploy web micro-apps on demand

Nikki can write a small web app, containerize it, deploy it to Cloud Run in your project, and hand you a live HTTPS URL. Each app is an independent Cloud Run service named `nikki-space-<slug>`.

## How to trigger a Space

Just ask, in chat. Nikki decides the framework and slug, shows you what she is about to deploy, and pauses at the approval gate. Examples:

- "Build me a Space: a mortgage payment calculator with amortization table."
- "Make a dashboard Space that shows the pipeline numbers I paste in as JSON."
- "Deploy a Streamlit Space that plots a CSV I upload."
- "Redeploy `mortgage-calc` with a dark theme." (same slug → updates in place)
- "Delete the `hello-test` Space."

Flow: **you ask → Nikki writes code → approval card (`deploy_space`) → Approve → status card updates live → URL**. Build time is 3–6 minutes. Nikki replies right after queuing; you don't have to keep the tab open — the result also lands in the gallery.

## Where to find deployed Spaces

- **Spaces Gallery** — header link "Spaces Gallery" or https://nikkiaia.com/spaces. Launch cards with status chips **Building Container → Pushing to Registry → Provisioning Cloud Run → Live**, plus the build log and error tail per Space. Same login as the chat.
- In chat: `list_spaces`, `space_status <slug>` (just ask Nikki "what spaces do I have?").

## Frameworks

| framework | `frontend_code` | `backend_code` (optional) | Use for |
|---|---|---|---|
| `fastapi` (default) | complete `index.html` (inline CSS/JS) | `main.py` exposing `app` (JSON APIs) | dashboards, calculators, trackers |
| `streamlit` | complete `app.py` | `helpers.py` | data apps, charts, CSV tools |
| `node` | `public/index.html` | Express `server.js` | JS-heavy front ends |

Extra pip/npm packages go in `extra_requirements` (one per line). `public=False` deploys IAM-protected instead of open to the internet.

## Security model

- Every Space runs as `nikki-spaces@nikkiaia-prod.iam.gserviceaccount.com`, which has **no roles**. Code inside a Space cannot read secrets, the database, or other services.
- `deploy_space` and `delete_space` always require your approval; `list_spaces`/`space_status` do not.
- Default Spaces are **public URLs** (anyone with the link). Don't put private data in a public Space; ask for `public=False` when it matters.
- Nikki's own service account has `run.admin`, `cloudbuild.builds.editor`, `serviceusage.serviceUsageConsumer`, `storage.objectAdmin`, `storage.admin` on the source-upload bucket only (`run-sources-nikkiaia-prod-us-east4`), a one-permission custom role `nikkiBucketLister` (`storage.buckets.list`), `artifactregistry.writer` (repo `cloud-run-source-deploy`) and `iam.serviceAccountUser` **only on** the spaces SA and the Cloud Build SA (`895240122598-compute@`). Never project-wide serviceAccountUser. Applied by `scripts/infra.sh` (idempotent).

## Cost

- A Space idles at min-instances 0 → ≈ $0 when unused; light use stays inside the Cloud Run free tier.
- Cloud Build: first 120 build-minutes/day free; a Space build is ~2–4 minutes.
- Artifact Registry: ~$0.10/GB-month for stored images (each Space image ≈ 150–400 MB). Delete Spaces you don't need.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Card shows **Failed** with `PERMISSION_DENIED` | IAM not applied → re-run `PROJECT=nikkiaia-prod REGION=us-east4 SKIP_SQL=1 bash scripts/infra.sh` |
| Failed with a pip/npm error in the log | Nikki's generated requirements are wrong — ask her to fix and redeploy the same slug |
| Failed: "container failed to start / port" | App isn't listening on `$PORT` (8080). Nikki's templates do this; custom `backend_code` must too |
| Deploy stuck > 20 min | Worker times out and marks it failed; check Cloud Build history in the GCP console |
| Gallery empty after Nikki said "queued" | Refresh; the page polls every 4 s while a build runs |

Build logs: GCP console → Cloud Build → History (project `nikkiaia-prod`, region `us-east4`). Services: Cloud Run → filter `nikki-space-`.

## Operator notes

- Config env (set by `scripts/deploy.sh`): `SPACES_PROJECT`, `SPACES_REGION` (us-east4), `SPACES_SERVICE_ACCOUNT`. Optional: `SPACES_WORKDIR` (default `/tmp/spaces`), `GCLOUD_BIN`.
- Metadata lives in the `spaces` table (Postgres in prod): slug, title, framework, status, stage_log, url, service_name, public, created_at, updated_at, last_error.
- gcloud is installed in Nikki's image (`google-cloud-cli` apt package) and authenticates through the Cloud Run metadata server — no key files anywhere.
