"""Pytest fixtures: point DB_PATH at a temp file before importing src."""
import os
import tempfile

import pytest

# Set env *before* anything from src is imported.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DB_PATH"] = _tmp.name
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("SESSION_SECRET", "test-secret")


@pytest.fixture(autouse=True)
async def fresh_db():
    """Reset schema before each test."""
    from src.db import init_db, connect
    # Drop existing tables so each test starts clean.
    async with connect() as db:
        for t in (
            "interactions",
            "contact_circles",
            "contact_phones",
            "contact_emails",
            "dismissed_duplicates",
            "contacts",
            "circles",
            "users",
        ):
            await db.execute(f"DROP TABLE IF EXISTS {t}")
        await db.commit()
    await init_db()
    yield
