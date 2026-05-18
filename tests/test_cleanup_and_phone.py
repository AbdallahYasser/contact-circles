"""Tests for the phoneless-cleanup route + phone-required enforcement."""
from src.db import connect
from src.routes.contacts import cleanup_no_phone, create_contact
from fastapi import HTTPException
import pytest


async def _seed_user() -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (999, "X"),
        )
        await db.commit()
        return cur.lastrowid


async def test_cleanup_deletes_only_phoneless_for_this_user():
    uid_a = await _seed_user()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)", (1000, "Y")
        )
        uid_b = cur.lastrowid
        for u, name, phone in [
            (uid_a, "WithPhone", "0100"),
            (uid_a, "NoPhone1", None),
            (uid_a, "NoPhone2", ""),
            (uid_a, "BlankSpaces", "   "),
            (uid_b, "OtherUserNoPhone", None),
        ]:
            await db.execute(
                "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
                (u, name, phone),
            )
        await db.commit()

    class FakeRequest:
        headers: dict = {}

    await cleanup_no_phone(request=FakeRequest(), user_id=uid_a)

    async with connect() as db:
        async with db.execute(
            "SELECT full_name FROM contacts WHERE user_id = ?", (uid_a,)
        ) as cur:
            names_a = sorted(r["full_name"] for r in await cur.fetchall())
        async with db.execute(
            "SELECT full_name FROM contacts WHERE user_id = ?", (uid_b,)
        ) as cur:
            names_b = sorted(r["full_name"] for r in await cur.fetchall())

    assert names_a == ["WithPhone"]  # only the one with a real phone survives
    assert names_b == ["OtherUserNoPhone"]  # other user untouched


async def test_create_contact_rejects_empty_phone():
    uid = await _seed_user()
    with pytest.raises(HTTPException) as exc:
        await create_contact(
            full_name="X",
            nickname="",
            phone="   ",
            telegram_handle="",
            birthday="",
            notes="",
            circle_ids=[],
            user_id=uid,
        )
    assert exc.value.status_code == 400
    assert "phone" in str(exc.value.detail).lower()
