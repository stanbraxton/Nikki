# Nikki as a multi-tenant service (Phase A)

## Model
- **Tenant** = one customer workspace (`tenants`). **Account** = a login (`accounts`, email + bcrypt password),
  belongs to one tenant with role `owner` | `member` | `admin`.
- The platform operator's tenant is `admin`; its account comes from `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH`
  and is (re)created at startup. Only `admin` sees the owner-only tools (files, SQL, self-maintenance, Spaces)
  and Gmail; everyone else gets chat, memory, web, integrations and schedules.
- Every chat turn / API request / scheduled run sets a `Principal` (tenant_id, email, role) in a context
  variable (`app/tenancy.py`). Tools and persistence filter on `tenant_id`; Chainlit already keeps chat
  history per login.

## Sign-up & login
- `/signup` — self-serve tenant creation. `SIGNUPS_ENABLED=false` closes it; `SIGNUP_CODE=xyz` requires an
  invite code. `/login` is Chainlit's login (a "Create an account" link is injected by `public/nikki.js`).
- No password reset yet (Phase B: email delivery needed).

## Integrations (`/integrations`)
- Catalogue in `app/integrations/providers.py`: Google (Drive + Calendar; Gmail admin-only), Microsoft 365,
  Tavily, Custom REST API. Add a provider = one entry there + a tools module.
- OAuth providers need a one-time developer registration: admin pastes client id/secret at `/admin`
  (stored encrypted in `provider_credentials`; env vars `GOOGLE_OAUTH_CLIENT_ID/SECRET`, `MS_OAUTH_CLIENT_ID/SECRET`
  are the fallback). Redirect URIs: `{PUBLIC_URL}/oauth/google/callback`, `{PUBLIC_URL}/oauth/microsoft/callback`.
- Connections live in `integrations` (per tenant, secret encrypted with Fernet; key = `TOKEN_ENCRYPTION_KEY`
  or derived from `CHAINLIT_AUTH_SECRET` — changing either invalidates stored credentials).
- API: `GET /api/integrations`, `POST /api/integrations/{provider}` (api-key form), `POST /api/integrations/{id}/disconnect`,
  admin: `GET/POST/DELETE /api/admin/providers[/{provider}]`.

## Schedules
- Cloud Scheduler job name = `nikki-sched-{tenant_short}-{label}`; the job body carries the global key and
  `/api/run` resolves the tenant from the `schedules` row. `/schedules` and `/api/schedules` show only the
  caller's tenant.

## Google verification note
Drive + Calendar are "sensitive" scopes (free verification). Gmail scopes are "restricted" (verification +
annual CASA assessment) and therefore requested for the admin tenant only. Flip `admin_scopes` → `scopes`
in providers.py and drop `admin_only` on the Gmail tools once verified.
