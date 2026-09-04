"""FastAPI service hosting Nikki: health/trace API plus the Chainlit UI at `/`."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from chainlit.utils import mount_chainlit
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import select

from app import persistence
from app.google_oauth import router as google_router
from app.legal import router as legal_router
from app.scheduler import router as scheduler_router
from app.spaces_gallery import router as spaces_router
from app.config import ROOT, settings
from app.tools import registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("nikki")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await persistence.init_db()
    log.info("tools loaded: %s", [t.name for t in registry.tools()])
    yield


app = FastAPI(title="Nikki", version="0.3.0", lifespan=lifespan, docs_url=None, redoc_url=None)


def _admin(authorization: str = Header(default="")) -> None:
    """Trace API is protected with a static bearer token (ADMIN_API_TOKEN)."""
    import os

    token = os.environ.get("ADMIN_API_TOKEN")
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(status_code=401)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "env": settings.env, "tools": len(registry.tools())}


@app.get("/api/traces/{thread_id}", dependencies=[Depends(_admin)])
async def traces(thread_id: str, limit: int = 200) -> list[dict]:
    async with persistence.engine().connect() as conn:
        rows = await conn.execute(
            select(persistence.traces).where(persistence.traces.c.thread_id == thread_id)
            .order_by(persistence.traces.c.ts.desc()).limit(limit)
        )
        return [dict(r._mapping) for r in rows]


@app.get("/api/skills", dependencies=[Depends(_admin)])
async def skills() -> list[dict]:
    return registry.skill_report()


app.include_router(spaces_router)
app.include_router(google_router)
app.include_router(scheduler_router)
app.include_router(legal_router)

mount_chainlit(app=app, target=str(ROOT / "app" / "ui.py"), path="/")
