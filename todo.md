# Nikki approval composer repair — 2026-09-17

- [x] Diagnose the approval prompt/composer behavior and failed deployment.
- [x] Identify the Cloud Run startup failure on revision 00047.
- [x] Make the smallest safe repair and add focused regression coverage.
- [ ] Push through the existing deployment pipeline (Stan approved production action).
- [ ] Verify live deployment and exercise an approval prompt on desktop + Android-compatible browser flow.

> The section above is from 2026-09-17 and predates the reasoning-upgrade work. Its two
> unchecked items were not verified in this session — confirm against the live service
> before acting on them, since the approval flow has changed since (durable `approvals`
> table, 5h TTL, nothing awaits a decision).

---

# Open items — handed off 2026-09-21

Context: `main` is at `bf046aa`, production is revision `nikki-00060-vzd`. The
reasoning-upgrade branch landed with every cost-affecting setting pinned to its
pre-merge value on the Cloud Run service, so per-turn cost should be flat and each
setting can be enabled individually and read against `/turns`.

## 1. `/account/password` — DONE 2026-09-25 (branch `fix-password-page`): all three fixed, routes enabled, no current password asked (session is the proof); see tests/test_password_page.py

The page (Nikki's own `e06c8ab`) is merged but **not reachable**: both `@router`
decorators are commented out at `app/auth.py:185` and `:190`, under a banner at
`:166` recording why. Handlers and `_password_form` are intact for this fix. It never
served a request in production, so re-enabling is its debut, not a restoration.

Three defects, all to be fixed in one reviewed commit:

- [x] **Admin password changes silently revert.** `authenticate()` verifies against
      `password_hash` in the accounts table (`app/auth.py:87`), and `password_submit`
      writes the new hash there — so the change works at first. But `ensure_admin()`
      (`app/auth.py:57`) overwrites the admin row with `settings.admin_password_hash`
      on every startup, commented "keep the env hash authoritative for the admin
      login". The next deploy or cold start restores the old password with no error
      and no log line. Non-admin accounts are unaffected. Decide deliberately whether
      the env var or the database is authoritative for the admin — the bug is that
      both currently claim to be.
- [x] **No rate limiting on the current-password check** — a password-guessing oracle.
- [x] **`_password_form(error=...)` interpolates into HTML unescaped** (`app/auth.py:247`).
      Not exploitable while every caller passes a literal, but one user-derived
      message away from being so.

Verify by **content, not status code**: Chainlit's SPA catch-all returns HTTP 200 for
any path, including `/definitely-not-a-real-path`. Grep the response for `Change
Password` / `Current password`, and use `/signup` returning `Create account` as the
control that proves the check can still detect a live route.

## 2. Error classifier treats deterministic 400s as retryable

- [ ] `_is_transient_error()` (`app/agent.py:156`) and `_BUG_ERRORS` (`:178`) came from
      Nikki's `da20842`. A `400 invalid_request_error` falls into the "unknown" bucket,
      so `friendly_error()` advises "Send the message once more". An HTTP 400 is
      deterministic by definition and will fail identically every time.

Real example from the canary: an `AnthropicInvalidRequestError` about
`thinking.type.enabled` produced "I cannot tell whether this is transient or a bug"
plus a retry suggestion. Treat provider 4xx (except 408/429) as deterministic.

## 3. Read-only agent profile — prerequisite for the MCP server

- [ ] Scoped but not started. Full design and estimate are in the session transcript;
      the two findings that shape it:
      - **Exposing "conversation" is transitively exposing every tool.** An MCP turn
        that runs the normal agent can reach `deploy_self`, `repo_commit_push` and
        `db_query` while answering. Restricting the MCP tool list does not restrict
        the agent's own registry — the deliverable is a second registry bound to an
        allowlist, with the graph built from it.
      - **A denylist is unsafe by construction.** `write_skill` saves to
        `/mnt/data/skills`, which hot-reloads on mtime, so Nikki authors new tools at
        runtime. A denylist exposes each new skill automatically. Allowlist by tool
        name, excluding dynamically-loaded skills by default.
      Also unresolved: approval gates assume a human in Chainlit. Under MCP there is
      nobody to click, so approval-requiring tools must hard-refuse, never
      auto-approve — confirm the headless path does not already bypass the gate.
      Estimate ≈ 4–5 days with a static bearer token; +2–4 if the connector requires
      OAuth 2.1 with dynamic client registration. Resolve that auth question first.

## 4. Build trigger latency is erratic

- [ ] `nikki-main-deploy` (us-east5) is correctly configured — enabled, `push.branch:
      ^main$`, GitHub connection `github-stan` — but fired **~22 minutes** after the
      push of `bf046aa`, and ~15½ minutes after Nikki's `e06c8ab`. Earlier builds fired
      in seconds (`da20842`: 4s).

This is long enough to look like a failure and provoke a manual
`gcloud builds triggers run`, which would double-deploy. **Before concluding a trigger
did not fire, check `gcloud builds list --ongoing` and allow at least 25 minutes.**
Worth investigating webhook delivery on the GitHub connection.

## 5. `requirements.txt` has no lockfile

- [ ] Only 6 of 32 requirements carry any version constraint, and
      `langchain-anthropic>=1.7.2,<2` is the only deliberate pin — added because the
      extended-thinking API changed under an unpinned dependency. The same class of
      break can still arrive through `langchain-core`, `langgraph`, or `chainlit`,
      and two builds from the same commit can resolve differently.

Consider a lockfile (`uv pip compile` / `pip-tools`) so a build is reproducible and a
dependency change is a reviewable diff rather than a surprise at deploy time.
