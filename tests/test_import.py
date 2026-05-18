"""Tests for vCard import — skip-no-phone, dedupe, circle assignment."""
from src.db import connect
from src.routes.import_ import (
    _extract_first_phone,
    _extract_name,
    _normalize_phone,
)


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
    assert _normalize_phone("+20 100 123 4567") == "201001234567"
    assert _normalize_phone("(02) 233-44-556") == "0223344556"
    assert _normalize_phone(None) == ""
    assert _normalize_phone("   ") == ""


def test_parse_vcard_extracts_name_and_phone():
    import vobject
    cards = list(vobject.readComponents(VCARD_SAMPLE))
    assert len(cards) == 4
    assert _extract_name(cards[0]) == "Ahmed Hassan"
    assert _extract_first_phone(cards[0]) == "+20 100 123 4567"
    assert _extract_first_phone(cards[1]) is None  # no phone


async def test_import_skips_no_phone_and_dedupes(tmp_path):
    """End-to-end test against the route logic without HTTP layer."""
    from src.routes.import_ import import_post
    from fastapi import UploadFile
    import io

    # Seed a user.
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (555, "T"),
        )
        user_id = cur.lastrowid
        await db.commit()

    # Build a fake UploadFile.
    class FakeRequest:
        headers: dict = {}

    file = UploadFile(filename="test.vcf", file=io.BytesIO(VCARD_SAMPLE.encode()))

    # Run twice: first import + immediate re-import (everything should dedupe).
    await import_post(request=FakeRequest(), file=file, circle_ids=[], user_id=user_id)

    async with connect() as db:
        async with db.execute(
            "SELECT full_name, phone FROM contacts WHERE user_id = ? ORDER BY id", (user_id,)
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    names = [r["full_name"] for r in rows]
    # 4 vCards: Ahmed (imported), No Phone (skipped), Sara (imported), Ahmed dup (skipped: same digits)
    assert "Ahmed Hassan" in names
    assert "Sara" in names
    assert "No Phone Person" not in names
    assert "Ahmed Duplicate" not in names
    assert len(rows) == 2

    # Re-import: zero new contacts (dedupe by phone).
    file2 = UploadFile(filename="test.vcf", file=io.BytesIO(VCARD_SAMPLE.encode()))
    await import_post(request=FakeRequest(), file=file2, circle_ids=[], user_id=user_id)

    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE user_id = ?", (user_id,)
        ) as cur:
            assert (await cur.fetchone())["n"] == 2


async def test_import_assigns_circles():
    from src.routes.import_ import import_post
    from fastapi import UploadFile
    import io

    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (666, "T"),
        )
        user_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)",
            (user_id, "Family"),
        )
        family_id = cur.lastrowid
        await db.commit()

    class FakeRequest:
        headers: dict = {}

    file = UploadFile(
        filename="x.vcf",
        file=io.BytesIO(b"BEGIN:VCARD\nVERSION:3.0\nFN:X\nTEL:0111\nEND:VCARD\n"),
    )
    await import_post(
        request=FakeRequest(), file=file, circle_ids=[family_id], user_id=user_id
    )

    async with connect() as db:
        async with db.execute(
            """
            SELECT cc.circle_id FROM contact_circles cc
            JOIN contacts c ON c.id = cc.contact_id
            WHERE c.user_id = ?
            """,
            (user_id,),
        ) as cur:
            rows = [r["circle_id"] for r in await cur.fetchall()]
    assert rows == [family_id]
