# M1 — Nikki engineer toolchain + Knowledge Base

- [ ] Infra: enable firebase/firebasehosting APIs, add Firebase to project, builder IAM (firebasehosting.admin, secret accessor), GITHUB_TOKEN secret placeholder
- [ ] Dockerfile: git + node 22 (for local lint/typecheck of small stuff; builds run in Cloud Build)
- [ ] app/tools/engineer.py: repo_open/list/read/write/edit/search, repo_git (status/log/diff), repo_commit_push (gated), repo_run (gated), register_app (gated), deploy_app (gated, Cloud Build async), build_status, list_apps
- [ ] app/engineer_build.py: cloudbuild.yaml generator (bun install → convex deploy → vite build → firebase deploy)
- [ ] apps table + builds table in Postgres; gallery: show apps
- [ ] Knowledge base: knowledge/*.md, app/tools/knowledge.py (kb_list/kb_read/kb_search + gated kb_write), index paragraph in system prompt
- [ ] Curated knowledge docs (Nikki, Stan, company, projects)
- [ ] docs/ENGINEER.md
- [ ] deploy, verify on prod (repo_open on Nikki repo w/o token? public? private → needs GITHUB_TOKEN), commit/push
- [ ] update skills/nikki
