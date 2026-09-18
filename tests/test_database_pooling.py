"""Keep Cloud SQL connection use bounded on Nikki's small database tier.

Run directly with ``python tests/test_database_pooling.py``; no extra test runner needed.
"""
from pathlib import Path
import os


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = (ROOT / "app" / "persistence.py").read_text()
UI = (ROOT / "app" / "ui.py").read_text()


def test_async_pool_has_one_connection_and_no_overflow():
    start = PERSISTENCE.index("def async_engine_kwargs")
    end = PERSISTENCE.index("def sync_engine_kwargs")
    config = PERSISTENCE[start:end]
    assert '"pool_size": _POOL_SIZE' in config
    assert '"max_overflow": _MAX_OVERFLOW' in config
    assert "_POOL_SIZE = 1" in PERSISTENCE
    assert "_MAX_OVERFLOW = 0" in PERSISTENCE


def test_chainlit_uses_the_same_limited_engine_settings():
    assert "return persistence.chainlit_data_layer()" in UI
    assert 'patch.object(chainlit_sqlalchemy, "create_async_engine", bounded_create_async_engine)' in PERSISTENCE
    assert "kwargs.update(async_engine_kwargs())" in PERSISTENCE


def test_chainlit_data_layer_binds_its_session_to_the_bounded_engine():
    # Import in a fresh local process with a syntactically valid PostgreSQL URL:
    # engine construction must not open a network connection.
    script = """\
from app import persistence
layer = persistence.chainlit_data_layer()
pool = layer.engine.pool
assert pool.size() == 1
assert pool._max_overflow == 0
assert layer.async_session.kw["bind"] is layer.engine
print("chainlit bounded layer instantiated")
"""
    import subprocess

    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "DATABASE_URL": "postgresql://u:p@localhost/nikki_test",
        "NIKKI_WORKSPACE_DIR": "/tmp/nikki-pool-workspace",
        "NIKKI_SKILLS_DIR": "/tmp/nikki-pool-skills",
    }
    result = subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
    assert "chainlit bounded layer instantiated" in result.stdout


def test_checkpointer_uses_one_shared_postgres_connection():
    assert "AsyncConnectionPool(" in PERSISTENCE
    assert "max_size=1" in PERSISTENCE
    assert "_checkpoint_setup_lock" in PERSISTENCE
    assert "if not _checkpoint_ready:" in PERSISTENCE


if __name__ == "__main__":
    test_async_pool_has_one_connection_and_no_overflow()
    test_chainlit_uses_the_same_limited_engine_settings()
    test_chainlit_data_layer_binds_its_session_to_the_bounded_engine()
    test_checkpointer_uses_one_shared_postgres_connection()
    print("database pooling regression checks passed")
