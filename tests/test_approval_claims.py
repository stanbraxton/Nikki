"""Durable approval authorization and duplicate-click regression checks.

Run directly with ``python tests/test_approval_claims.py``.  The test only uses a
temporary local SQLite database; no provider, browser, or production database call.
"""
import asyncio
import os
import tempfile
from datetime import timedelta


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/approvals.db"
        os.environ["NIKKI_WORKSPACE_DIR"] = f"{tmp}/workspace"
        os.environ["NIKKI_SKILLS_DIR"] = f"{tmp}/skills"

        from app import persistence

        await persistence.init_db()

        # The owner gets exactly one atomic claim.  A duplicate click cannot move
        # an approved-running action back to pending or execute it a second time.
        approval_id = await persistence.create_pending_approval(
            "thread-1", "tenant-a", "owner@example.com", ["write_file"]
        )
        assert await persistence.claim_pending_approval(
            "thread-1", "tenant-a", "owner@example.com", True, approval_id
        ) == (approval_id, "claimed")
        assert await persistence.approval_tool_names(approval_id) == ["write_file"]
        assert await persistence.claim_pending_approval(
            "thread-1", "tenant-a", "owner@example.com", True, approval_id
        ) == (approval_id, "approved_running")
        await persistence.finish_approval(approval_id)
        assert await persistence.claim_pending_approval(
            "thread-1", "tenant-a", "owner@example.com", True, approval_id
        ) == (approval_id, "completed")

        # An approval record cannot be claimed by a different tenant/user, even
        # when they know the UUID or submit a copied button payload.
        protected = await persistence.create_pending_approval(
            "thread-2", "tenant-a", "owner@example.com", ["db_execute"]
        )
        assert await persistence.claim_pending_approval(
            "thread-2", "tenant-b", "other@example.com", True, protected
        ) == (None, "not_owner")
        assert await persistence.claim_pending_approval(
            "thread-other", "tenant-a", "owner@example.com", True, protected
        ) == (None, "not_owner")

        # Expired approvals never become runnable.
        expired = await persistence.create_pending_approval(
            "thread-3", "tenant-a", "owner@example.com", ["gmail_send"],
            ttl=timedelta(seconds=-1),
        )
        assert await persistence.claim_pending_approval(
            "thread-3", "tenant-a", "owner@example.com", True, expired
        ) == (expired, "expired")

        # A replacement message supersedes a still-pending action, not one that
        # another instance has claimed and may already be executing.
        assert await persistence.supersede_pending_approval(
            "thread-2", "tenant-a", "owner@example.com"
        ) == "ok"
        assert await persistence.claim_pending_approval(
            "thread-2", "tenant-a", "owner@example.com", True, protected
        ) == (protected, "superseded")

        running = await persistence.create_pending_approval(
            "thread-4", "tenant-a", "owner@example.com", ["gmail_send"]
        )
        assert (await persistence.claim_pending_approval(
            "thread-4", "tenant-a", "owner@example.com", True, running
        ))[1] == "claimed"
        assert await persistence.supersede_pending_approval(
            "thread-4", "tenant-a", "owner@example.com"
        ) == "running"

        # The database index prevents two instances from simultaneously creating
        # separate pending authorizations for one paused conversation.
        first = await persistence.create_pending_approval(
            "thread-5", "tenant-a", "owner@example.com", ["write_file"]
        )
        second = await persistence.create_pending_approval(
            "thread-5", "tenant-a", "owner@example.com", ["write_file"]
        )
        assert first != second
        assert await persistence.claim_pending_approval(
            "thread-5", "tenant-a", "owner@example.com", True, first
        ) == (first, "superseded")
        assert await persistence.claim_pending_approval(
            "thread-5", "tenant-a", "owner@example.com", True, second
        ) == (second, "claimed")

        await persistence.close_db()


if __name__ == "__main__":
    asyncio.run(main())
    print("approval claim regression checks passed")
