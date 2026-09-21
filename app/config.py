"""Central configuration for Nikki. Everything comes from environment variables
(Secret Manager injects the sensitive ones on Cloud Run; .env is for local dev)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    app_name: str = "Nikki"
    # LLM
    model: str = _env("NIKKI_MODEL", "anthropic:claude-sonnet-5")
    anthropic_api_key: str | None = _env("ANTHROPIC_API_KEY")
    openai_api_key: str | None = _env("OPENAI_API_KEY")
    # Used automatically when the primary provider rejects a call for billing reasons
    # (e.g. Anthropic "credit balance is too low"). Empty string disables the fallback.
    # This MUST stay on a different provider than `model`: an Anthropic fallback would share
    # the exhausted account and fail identically. gpt-4.1 rather than gpt-4.1-mini, so a paying
    # subscriber who lands here still gets a usable answer rather than a visible downgrade.
    fallback_model: str | None = _env("NIKKI_FALLBACK_MODEL", "openai:gpt-4.1")
    # Approx. token budget for conversation history sent to the model (system prompt excluded).
    # The small default protects the OpenAI fallback (30k TPM org cap); Anthropic models get a much
    # larger window so long debugging threads keep their earlier errors and findings.
    history_budget_tokens: int = int(_env("NIKKI_HISTORY_BUDGET_TOKENS", "24000"))
    history_budget_tokens_anthropic: int = int(_env("NIKKI_HISTORY_BUDGET_TOKENS_ANTHROPIC", "80000"))
    # Tool results from earlier turns are shortened to this many chars before being re-sent (0 = off).
    # Raised from 1500: compaction rewrites messages that were part of the PREVIOUS turn's cached
    # prefix, so an aggressive limit both strips the evidence a follow-up question needs and
    # invalidates the Anthropic prompt cache from that point on - paying full price for less
    # context. trim_history runs first, so this only ever shrinks a history already inside budget.
    old_tool_result_chars: int = int(_env("NIKKI_OLD_TOOL_RESULT_CHARS", "6000"))
    max_tokens: int = int(_env("NIKKI_MAX_TOKENS", "8192"))
    # Graph-level step cap. Was 40 - higher than LangGraph's own default of 25,
    # so a looping turn ran nearly twice as long before anything stopped it.
    recursion_limit: int = int(_env("NIKKI_RECURSION_LIMIT", "18"))
    # Tool rounds in one user turn (one round = one model call plus its tools).
    max_tool_rounds: int = int(_env("NIKKI_MAX_TOOL_ROUNDS", "16"))
    # How many times one tool may be called with identical arguments in a turn
    # before the call is refused. 2 allows a legitimate retry; 3+ is a loop.
    max_repeated_tool_calls: int = int(_env("NIKKI_MAX_REPEATED_TOOL_CALLS", "2"))
    # Hard ceiling on input+output tokens for one turn. 0 disables.
    turn_token_ceiling: int = int(_env("NIKKI_TURN_TOKEN_CEILING", "400000"))
    # Extended thinking budget, Anthropic only. 0 = off. Raises max_tokens when set.
    # On by default: the largest single lever on answer quality in this app. The UI has always
    # rendered thinking blocks (stream_segment handles them); the model was never asked for any.
    thinking_budget_tokens: int = int(_env("NIKKI_THINKING_BUDGET_TOKENS", "4000"))
    # Effort for models that use ADAPTIVE thinking (Sonnet 5, Opus 5, Opus 4.7+), which
    # replaced the fixed budget above - those models reject budget_tokens with a 400.
    # thinking_budget_tokens is still the on/off switch for both kinds; this sets depth
    # for the adaptive kind. "high" is the API default, so "medium" is the cheaper knob.
    # Valid: low | medium | high | xhigh | max.
    thinking_effort: str = _env("NIKKI_THINKING_EFFORT", "medium") or "medium"
    # Model for turns that touch the engineer toolchain. Empty = no routing.
    # Deliberately the SAME model as `model` above for now, so routing is live and exercised
    # but costs nothing extra. Extended thinking and the sonnet-5 bump ship as one change and
    # their cost impact can be read on its own; stacking an Opus-class model on engineering
    # turns is a separate decision, made once there is a bill to compare against.
    engineer_model: str = _env("NIKKI_ENGINEER_MODEL", "anthropic:claude-sonnet-5") or ""
    # Persistence. Postgres in prod (postgresql://user:pw@/db?host=/cloudsql/...),
    # SQLite locally.
    database_url: str = _env("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'nikki.db'}")
    # Database the db_query tool is allowed to hit (defaults to the app DB).
    tool_database_url: str | None = _env("TOOL_DATABASE_URL")
    # Durable directories (GCS volume mounts on Cloud Run).
    workspace_dir: Path = Path(_env("NIKKI_WORKSPACE_DIR", str(ROOT / "data" / "workspace")))
    skills_dir: Path = Path(_env("NIKKI_SKILLS_DIR", str(ROOT / "data" / "skills")))
    # Auth
    admin_username: str = _env("ADMIN_USERNAME", "admin")
    admin_password_hash: str | None = _env("ADMIN_PASSWORD_HASH")  # bcrypt
    # Runtime
    port: int = int(_env("PORT", "8080"))
    env: str = _env("NIKKI_ENV", "dev")
    persona: str = field(default_factory=lambda: _env(
        "NIKKI_PERSONA",
        "You are Nikki, Stan Braxton's private AI assistant. You are direct, concise, "
        "and practical. You use tools when they help, and never "
        "pretend to have done something you did not do. Any action with side effects "
        "must go through an approval gate; if the user declines, respect it.",
    ))

    tenant_persona: str = field(default_factory=lambda: _env(
        "NIKKI_TENANT_PERSONA",
        "You are Nikki, the user's private AI assistant. You are direct, concise, and practical. You use "
        "tools when they help, and never pretend to have done something you did not do. "
        "Any action with side effects must go through an approval gate; if the user declines, respect it. "
        "You only ever see this user's own data; never reference other users.",
    ))

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgres")

    @property
    def sqlalchemy_async_url(self) -> str:
        """Async driver URL for SQLAlchemy (Chainlit data layer + traces)."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+asyncpg://", 1)
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return url

    @property
    def sqlalchemy_sync_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        return url


settings = Settings()

for _d in (settings.workspace_dir, settings.skills_dir):
    _d.mkdir(parents=True, exist_ok=True)
if not settings.is_postgres:
    Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
