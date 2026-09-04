# Engineer toolchain & Knowledge Base

Nikki can maintain real software projects: GitHub repositories built by Cloud Build and hosted on Firebase Hosting,
with an optional Convex backend. This is how Stan's web apps are maintained after they move off the old platform.

## One-time setup
1. **GitHub token** — create a fine-grained personal access token (repos: Contents read/write, Administration
   read/write for repo creation) and store it: `printf '%s' "$TOKEN" | gcloud secrets versions add GITHUB_TOKEN --data-file=- --project nikkiaia-prod`.
   The service reads `GITHUB_TOKEN` on the next revision/instance (redeploy or `gcloud run services update nikki --region us-east4 --update-secrets GITHUB_TOKEN=GITHUB_TOKEN:latest`).
2. **Firebase** — already added to project `nikkiaia-prod`. Sites are created on demand by `register_app`.
3. **Convex** — sign in at dashboard.convex.dev (GitHub login). For each app create a project, open Settings → Deploy
   keys → *Generate production deploy key*, and hand it to Nikki via `register_app`. Nikki stores it in Secret Manager
   (`nikki-app-{slug}-convex`) and grants Cloud Build read access; it never appears in chat history or the database.

## Tools
| Tool | Gate | What it does |
|---|---|---|
| `repo_open owner/name [branch]` | — | clone/refresh into `/tmp/repos`, show layout |
| `repo_list`, `repo_read`, `repo_search` | — | browse a repo (`git grep` regex search) |
| `repo_write`, `repo_edit` | — | change files in the local working tree |
| `repo_git "<args>"` | — | local git: status, diff, log, checkout -b, add, reset, restore… |
| `repo_run "<cmd>"` | approval | shell inside the repo (tsc, biome, unit tests). 1 GiB RAM, no secrets in env |
| `repo_commit_push msg [branch]` | approval | commit as Nikki <nikki@nikkiaia.com> and push |
| `github_create_repo name` | approval | new private repo under the token owner |
| `register_app slug title repo [convex_deploy_key] [firebase_site]` | approval | register a deployable app |
| `deploy_app slug` | approval | Cloud Build: bun install → convex deploy + build → firebase deploy |
| `app_status`, `build_log`, `list_apps` | — | follow builds |
| `add_custom_domain slug domain` | approval | attach a domain; returns DNS records to create |
| `delete_app slug` | approval | unregister (repo + Convex untouched) |

Only the admin tenant sees these tools. Deploys only ship **committed** HEAD; uncommitted changes are rejected.
The build generates `cloudbuild.yaml` and, if the repo lacks one, a `firebase.json` (SPA rewrite to `/index.html`,
public dir = `build_dir`, default `dist`). Frontends read the production Convex URL from `VITE_CONVEX_URL`, which
`convex deploy --cmd` injects during the build.

## Example session
> Open stanbraxton/spinwheel, find where the wheel colours are defined, make the default palette warmer, run the
> typecheck, then push and deploy.

Nikki: `repo_open` → `repo_search "palette|colors"` → `repo_read src/lib/wheel.ts` → `repo_edit …` →
`repo_run "bunx tsc --noEmit"` (approval) → `repo_commit_push "Warmer default palette"` (approval) →
`deploy_app spinwheel` (approval) → later `app_status spinwheel`.

## Knowledge Base
`knowledge/*.md` in the Nikki repo holds curated documents about Stan, his company, this system and every project.
A one-line index is in Nikki's system prompt; she reads documents on demand with `kb_read`, searches with
`kb_search`, and records new durable facts with `kb_write` (approval; saved to the persistent overlay
`/mnt/data/knowledge`, which wins over the repo copy of the same name). To promote overlay edits into the repo, copy
them into `knowledge/` and commit. Never store secrets in the knowledge base.
