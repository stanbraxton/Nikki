# Gmail & Google Drive in Nikki

Nikki reads and (with your approval) writes Gmail and Drive through Google's official APIs, using
an OAuth connection that you grant once per Google account. Tokens live in Nikki's Postgres
database (`google_tokens`), never in the chat or logs.

## One-time setup (project owner, ~10 minutes)

1. Google Cloud console → project **nikkiaia-prod** → **APIs & Services → Library**: enable
   **Gmail API** and **Google Drive API**.
2. **APIs & Services → OAuth consent screen**: User type *External*, app name `Nikki`, your email
   as support/developer contact. Add scopes `.../auth/gmail.modify` and `.../auth/drive`.
   Then **Publish app → In production**. (In *Testing* mode Google expires refresh tokens after
   7 days and you would have to reconnect weekly.) Google may show an "unverified app" warning
   because the app is private — click *Advanced → Go to Nikki* when connecting; that is expected.
3. **Credentials → Create credentials → OAuth client ID**: type *Web application*, name `Nikki`,
   Authorized redirect URI **exactly** `https://nikkiaia.com/oauth/google/callback`.
4. Copy the Client ID and Client secret into Secret Manager (each as a new version):
   ```bash
   printf '%s' 'CLIENT_ID'     | gcloud secrets versions add GOOGLE_OAUTH_CLIENT_ID     --data-file=-
   printf '%s' 'CLIENT_SECRET' | gcloud secrets versions add GOOGLE_OAUTH_CLIENT_SECRET --data-file=-
   ```
   or in the console: Security → Secret Manager → the secret → **New version**.
5. Redeploy or restart Nikki so the new secret versions load
   (`gcloud run services update nikki --region us-east4 --update-secrets=GOOGLE_OAUTH_CLIENT_ID=GOOGLE_OAUTH_CLIENT_ID:latest,GOOGLE_OAUTH_CLIENT_SECRET=GOOGLE_OAUTH_CLIENT_SECRET:latest`).

## Connecting an account

Log in to https://nikkiaia.com, then open **https://nikkiaia.com/connect/google** and pick the
Google account. Repeat for each account you want Nikki to see (personal, Green Collar, …).
`GET /api/google/accounts` lists connected accounts; `POST /api/google/disconnect/{email}` removes one.

## What Nikki can do

| Tool | Approval? | Example prompt |
|---|---|---|
| `google_accounts` | no | "Which Google accounts are connected?" |
| `gmail_search` | no | "Any unread emails from the bank this week?" |
| `gmail_read` | no | "Read me the latest one from Sarah." |
| `gmail_send` | **yes** | "Reply and tell them Thursday works." |
| `drive_search` | no | "Find the Q3 lease spreadsheet in Drive." |
| `drive_read` | no | "Summarize that doc." (Docs, Sheets, PDFs, text) |
| `drive_create_doc` | **yes** | "Save this summary as a Google Doc." |

With several accounts connected, say which one ("check my Green Collar inbox"); otherwise Nikki
uses the first connected account.

## Security notes

- Scopes requested: `gmail.modify`, `drive`, `openid`, `email`. Nikki never deletes mail.
- Sending mail or creating files always stops at an approval gate in the chat UI. In scheduled
  (unattended) runs those tools are refused unless the schedule was created with `auto_approve`.
- To revoke everything: https://myaccount.google.com/permissions → remove *Nikki*, then
  `POST /api/google/disconnect/{email}`.
