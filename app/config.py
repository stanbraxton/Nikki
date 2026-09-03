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
    model: str = _env("NIKKI_MODEL", "anthropic:claude-sonnet-4-5")
    anthropic_api_key: str | None = _env("ANTHROPIC_API_KEY")
    openai_api_key: str | None = _env("OPENAI_API_KEY")
    max_tokens: int = int(_env("NIKKI_MAX_TOKENS", "4096"))
    recursion_limit: int = int(_env("NIKKI_RECURSION_LIMIT", "40"))
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
        "and practical. You reason step by step, use tools when they help, and never "
        "pretend to have done something you did not do. Any action with side effects "
        "must go through an approval gate; if the user declines, respect it.",
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
