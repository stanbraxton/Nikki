"""Provider catalogue. Two kinds:
- oauth2: user clicks Connect → provider consent → refresh token stored per tenant.
  Needs a one-time developer app registration (client id/secret) entered by the admin
  at /admin (falls back to env vars for Google).
- apikey: user pastes a key (plus optional fields) into a form on /integrations.

Scopes can differ by role: Gmail is restricted-scope (needs Google CASA for public apps), so
only the admin tenant asks for it until Nikki passes verification."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    secret: bool = False
    placeholder: str = ""
    required: bool = True


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    kind: str  # oauth2 | apikey
    description: str
    icon: str  # emoji for the card
    tools: tuple[str, ...] = ()
    # oauth2
    auth_url: str = ""
    token_url: str = ""
    userinfo_url: str = ""
    scopes: tuple[str, ...] = ()
    admin_scopes: tuple[str, ...] = ()  # extra scopes for the admin tenant
    extra_auth_params: dict = field(default_factory=dict)
    env_client_id: str = ""
    env_client_secret: str = ""
    setup_hint: str = ""
    # apikey
    fields: tuple[Field, ...] = ()
    multi: bool = False  # allow several connections per tenant


PROVIDERS: dict[str, Provider] = {
    "google": Provider(
        id="google", name="Google Workspace", kind="oauth2", icon="🟢",
        description="Google Drive and Calendar (Gmail for the admin account).",
        tools=("drive_search", "drive_read", "drive_create_doc", "gcal_list_events", "gcal_create_event"),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scopes=("openid", "https://www.googleapis.com/auth/userinfo.email",
                "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/calendar"),
        admin_scopes=("https://www.googleapis.com/auth/gmail.modify",),
        extra_auth_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        env_client_id="GOOGLE_OAUTH_CLIENT_ID", env_client_secret="GOOGLE_OAUTH_CLIENT_SECRET",
        setup_hint="Google Cloud console → Google Auth Platform → Clients → Web application; redirect URI {public_url}/oauth/google/callback.",
        multi=True,
    ),
    "microsoft": Provider(
        id="microsoft", name="Microsoft 365", kind="oauth2", icon="🟦",
        description="Outlook mail, Calendar and OneDrive via Microsoft Graph.",
        tools=("outlook_search", "outlook_read", "outlook_send", "mscal_list_events", "onedrive_search", "onedrive_read"),
        auth_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        userinfo_url="https://graph.microsoft.com/v1.0/me",
        scopes=("openid", "email", "offline_access", "User.Read", "Mail.Read", "Mail.Send", "Calendars.ReadWrite", "Files.ReadWrite"),
        extra_auth_params={"response_mode": "query"},
        env_client_id="MS_OAUTH_CLIENT_ID", env_client_secret="MS_OAUTH_CLIENT_SECRET",
        setup_hint="Azure portal → Microsoft Entra ID → App registrations → New (accounts in any org directory + personal); Web redirect URI {public_url}/oauth/microsoft/callback; Certificates & secrets → new client secret.",
        multi=True,
    ),
    "tavily": Provider(
        id="tavily", name="Tavily Search", kind="apikey", icon="🔎",
        description="Higher-quality web search results for web_search (DuckDuckGo is used without it).",
        tools=("web_search",),
        fields=(Field("api_key", "API key", secret=True, placeholder="tvly-..."),),
    ),
    "custom_rest": Provider(
        id="custom_rest", name="Custom REST API", kind="apikey", icon="🧩",
        description="Any HTTP API with a key: Nikki gets a call_api tool for it.",
        tools=("call_api",),
        fields=(
            Field("label", "Name (how you'll refer to it)", placeholder="crm"),
            Field("base_url", "Base URL", placeholder="https://api.example.com/v1"),
            Field("auth_header", "Auth header name", placeholder="Authorization"),
            Field("auth_prefix", "Auth value prefix", placeholder="Bearer ", required=False),
            Field("api_key", "API key / token", secret=True),
            Field("notes", "Notes for Nikki (endpoints, quirks)", required=False),
        ),
        multi=True,
    ),
}


def get(provider_id: str) -> Provider:
    p = PROVIDERS.get(provider_id)
    if p is None:
        raise KeyError(provider_id)
    return p
