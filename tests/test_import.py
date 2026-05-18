"""Tests for vCard import — multi-phone/email extraction + auto-merge."""
import io

import vobject
from fastapi import UploadFile

from src.db import connect
from src.normalize import normalize_phone
from src.routes.import_ import (
    _extract_emails,
    _extract_name,
    _extract_phones,
    import_post,
)


class FakeRequest:
    headers: dict = {}


VCARD_MULTI = """BEGIN:VCARD
VERSION:3.0
FN:Multi Person
TEL;TYPE=CELL:+20 100 111 1111
TEL;TYPE=HOME:+20 200 222 2222
TEL;TYPE=WORK:+20 300 333 3333
EMAIL;TYPE=HOME:multi@home.com
EMAIL;TYPE=WORK:multi@work.com
END:VCARD
"""

VCARD_SAMPLE = """BEGIN:VCARD
VERSION:3.0
FN:Ahmed Hassan
TEL;TYPE=CELL:+20 100 123 4567
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:No Phone Person
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Sara
TEL;TYPE=HOME:0223344556
END:VCARD
BEGIN:VCARD
VERSION:3.0
FN:Ahmed Duplicate
TEL;TYPE=CELL:+20-100-123-4567
END:VCARD
"""


def test_normalize_phone_strips_punctuation():
    assert normalize_phone("+20 100 123 4567") == "201001234567"
    assert normalize_phone("(02) 233-44-556") == "0223344556"
    assert normalize_phone(None) == ""


def test_parse_vcard_extracts_all_phones_with_labels():
    card = next(vobject.readComponents(VCARD_MULTI))
    assert _extract_name(card) == "Multi Person"
    phones = _extract_phones(card)
    assert [p[0] for p in phones] == ["+20 100 111 1111", "+20 200 222 2222", "+20 300 333 3333"]
    assert [p[1] for p in phones] == ["mobile", "home", "work"]
    emails = _extract_emails(card)
    assert [e[0] for e in emails] == ["multi@home.com", "multi@work.com"]
    assert [e[1] for e in emails] == ["home", "work"]


async def _seed_user(tg_id=555) -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)", (tg_id, "T")
        )
        await db.commit()
        return cur.lastrowid


async def test_import_stores_all_phones_and_emails_for_new_contact():
    uid = await _seed_user()
    file = UploadFile(filename="m.vcf", file=io.BytesIO(VCARD_MULTI.encode()))
    await import_post(request=FakeRequest(), file=file, circle_ids=[], user_id=uid)

    async with connect() as db:
        async with db.execute(
            "SELECT id FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            cid = (await cur.fetchone())["id"]
        async with db.execute(
            "SELECT label FROM contact_phones WHERE contact_id = ? ORDER BY id", (cid,)
        ) as cur:
            assert [r["label"] for r in await cur.fetchall()] == ["mobile", "home", "work"]
        async with db.execute(
            "SELECT label FROM contact_emails WHERE contact_id = ? ORDER BY id", (cid,)
        ) as cur:
            assert [r["label"] for r in await cur.fetchall()] == ["home", "work"]


async def test_import_skips_no_phone_and_dedupes_within_file():
    uid = await _seed_user()
    file = UploadFile(filename="s.vcf", file=io.BytesIO(VCARD_SAMPLE.encode()))
    await import_post(request=FakeRequest(), file=file, circle_ids=[], user_id=uid)

    async with connect() as db:
        async with db.execute(
            "SELECT full_name FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            names = sorted(r["full_name"] for r in await cur.fetchall())
    # Ahmed Hassan + Sara only; "No Phone" skipped, "Ahmed Duplicate" deduped within file
    assert names == ["Ahmed Hassan", "Sara"]


async def test_reimport_auto_merges_into_existing_by_phone():
    """First create a contact via the form, then re-import a vCard with same number,
    different name. It should merge into the existing record, not create a new one."""
    uid = await _seed_user(tg_id=556)
    # Manually create the existing contact + a phone row.
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "Ahmed Hassan", "+20 100 123 4567"),
        )
        existing_id = cur.lastrowid
        await db.execute(
            """
            INSERT INTO contact_phones (contact_id, value, value_norm, label, is_primary)
            VALUES (?, ?, ?, 'mobile', 1)
            """,
            (existing_id, "+20 100 123 4567", "201001234567"),
        )
        await db.commit()

    # vCard uses same digits "+20-100-123-4567" -> normalized "201001234567"
    file = UploadFile(
        filename="m.vcf",
        file=io.BytesIO(
            b"BEGIN:VCARD\nVERSION:3.0\nFN:Ahmed H.\nTEL;TYPE=HOME:+20-100-123-4567\nEMAIL:ah@x.com\nEND:VCARD\n"
        ),
    )
    await import_post(request=FakeRequest(), file=file, circle_ids=[], user_id=uid)

    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            assert (await cur.fetchone())["n"] == 1  # merged, not inserted
        async with db.execute(
            "SELECT full_name FROM contacts WHERE id = ?", (existing_id,)
        ) as cur:
            # Name preserved — we don't overwrite.
            assert (await cur.fetchone())["full_name"] == "Ahmed Hassan"
        async with db.execute(
            "SELECT value FROM contact_emails WHERE contact_id = ?", (existing_id,)
        ) as cur:
            assert [r["value"] for r in await cur.fetchall()] == ["ah@x.com"]


async def test_reimport_auto_merges_by_email_when_phone_differs():
    uid = await _seed_user(tg_id=557)
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "Ali Old", "111"),
        )
        existing_id = cur.lastrowid
        await db.execute(
            "INSERT INTO contact_phones (contact_id, value, value_norm, label, is_primary) VALUES (?, ?, ?, ?, 1)",
            (existing_id, "111", "111", "mobile"),
        )
        await db.execute(
            "INSERT INTO contact_emails (contact_id, value, value_norm, label) VALUES (?, ?, ?, ?)",
            (existing_id, "ali@x.com", "ali@x.com", "home"),
        )
        await db.commit()

    # Different phone, same email -> should merge.
    file = UploadFile(
        filename="e.vcf",
        file=io.BytesIO(
            b"BEGIN:VCARD\nVERSION:3.0\nFN:Ali New\nTEL:999\nEMAIL:ALI@X.COM\nEND:VCARD\n"
        ),
    )
    await import_post(request=FakeRequest(), file=file, circle_ids=[], user_id=uid)

    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            assert (await cur.fetchone())["n"] == 1
        async with db.execute(
            "SELECT value FROM contact_phones WHERE contact_id = ? ORDER BY id", (existing_id,)
        ) as cur:
            phones = [r["value"] for r in await cur.fetchall()]
            assert "111" in phones and "999" in phones


async def test_import_attaches_imported_to_circles_even_when_merging():
    uid = await _seed_user(tg_id=558)
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)", (uid, "School")
        )
        school = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "X", "555"),
        )
        existing = cur.lastrowid
        await db.execute(
            "INSERT INTO contact_phones (contact_id, value, value_norm, label, is_primary) VALUES (?, ?, ?, 'mobile', 1)",
            (existing, "555", "555"),
        )
        await db.commit()

    file = UploadFile(
        filename="x.vcf",
        file=io.BytesIO(b"BEGIN:VCARD\nVERSION:3.0\nFN:X New\nTEL:555\nEND:VCARD\n"),
    )
    await import_post(
        request=FakeRequest(), file=file, circle_ids=[school], user_id=uid
    )
    async with connect() as db:
        async with db.execute(
            "SELECT circle_id FROM contact_circles WHERE contact_id = ?", (existing,)
        ) as cur:
            assert [r["circle_id"] for r in await cur.fetchall()] == [school]
