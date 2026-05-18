"""Tests for multi-phone / multi-email storage + primary-phone sync."""
from src.db import connect
from src.routes.contacts import create_contact, update_contact


async def _seed_user() -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)", (4242, "X")
        )
        await db.commit()
        return cur.lastrowid


async def test_create_contact_stores_multiple_phones_and_emails():
    uid = await _seed_user()
    await create_contact(
        full_name="Multi",
        nickname="",
        phone_value=["+20 100 1", "+20 200 2", "+20 300 3"],
        phone_label=["mobile", "home", "work"],
        email_value=["one@x.com", "two@x.com"],
        email_label=["home", "work"],
        telegram_handle="",
        birthday="",
        notes="",
        circle_ids=[],
        user_id=uid,
    )
    async with connect() as db:
        async with db.execute(
            "SELECT id, phone FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
            cid = row["id"]
            assert row["phone"] == "+20 100 1"  # first one is primary cache
        async with db.execute(
            "SELECT value, label, is_primary FROM contact_phones WHERE contact_id = ? ORDER BY id",
            (cid,),
        ) as cur:
            phones = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT value, label FROM contact_emails WHERE contact_id = ? ORDER BY id",
            (cid,),
        ) as cur:
            emails = [dict(r) for r in await cur.fetchall()]

    assert [p["value"] for p in phones] == ["+20 100 1", "+20 200 2", "+20 300 3"]
    assert [p["label"] for p in phones] == ["mobile", "home", "work"]
    primary_flags = [p["is_primary"] for p in phones]
    assert primary_flags == [1, 0, 0]
    assert [e["value"] for e in emails] == ["one@x.com", "two@x.com"]


async def test_update_contact_replaces_phones_and_resyncs_primary():
    uid = await _seed_user()
    await create_contact(
        full_name="C", nickname="",
        phone_value=["111", "222"], phone_label=["mobile", "home"],
        email_value=[], email_label=[],
        telegram_handle="", birthday="", notes="",
        circle_ids=[], user_id=uid,
    )
    async with connect() as db:
        async with db.execute(
            "SELECT id FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            cid = (await cur.fetchone())["id"]

    await update_contact(
        contact_id=cid,
        full_name="C", nickname="",
        phone_value=["999", "888"], phone_label=["work", "home"],
        email_value=["x@x.com"], email_label=["home"],
        telegram_handle="", birthday="", notes="",
        circle_ids=[], user_id=uid,
    )

    async with connect() as db:
        async with db.execute(
            "SELECT phone FROM contacts WHERE id = ?", (cid,)
        ) as cur:
            assert (await cur.fetchone())["phone"] == "999"
        async with db.execute(
            "SELECT value FROM contact_phones WHERE contact_id = ? ORDER BY id", (cid,)
        ) as cur:
            assert [r["value"] for r in await cur.fetchall()] == ["999", "888"]
        async with db.execute(
            "SELECT value FROM contact_emails WHERE contact_id = ?", (cid,)
        ) as cur:
            assert [r["value"] for r in await cur.fetchall()] == ["x@x.com"]


async def test_duplicate_phone_in_form_is_deduped_per_contact():
    """Same number listed in different formats → one row (UNIQUE on normalized digits)."""
    uid = await _seed_user()
    await create_contact(
        full_name="C", nickname="",
        phone_value=["+20 100", "20100", "20-100"],  # all normalize to "20100"
        phone_label=["mobile", "home", "work"],
        email_value=[], email_label=[],
        telegram_handle="", birthday="", notes="",
        circle_ids=[], user_id=uid,
    )
    async with connect() as db:
        async with db.execute(
            "SELECT cp.value FROM contact_phones cp JOIN contacts c ON c.id = cp.contact_id WHERE c.user_id = ?",
            (uid,),
        ) as cur:
            vals = [r["value"] for r in await cur.fetchall()]
    assert len(vals) == 1
