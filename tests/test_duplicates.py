"""Tests for /duplicates: cluster detection, merge, dismiss."""
from src.db import connect
from src.routes.duplicates import (
    _build_clusters,
    count_duplicates_for_user,
    dismiss_duplicate,
    merge_duplicates,
)


async def _seed_user(tg=100) -> int:
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)", (tg, "X")
        )
        await db.commit()
        return cur.lastrowid


async def _add_contact(uid: int, name: str, phones: list[str], emails: list[str] | None = None) -> int:
    emails = emails or []
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, name, phones[0] if phones else None),
        )
        cid = cur.lastrowid
        for i, p in enumerate(phones):
            from src.normalize import normalize_phone
            await db.execute(
                """
                INSERT INTO contact_phones (contact_id, value, value_norm, label, is_primary)
                VALUES (?, ?, ?, 'mobile', ?)
                """,
                (cid, p, normalize_phone(p), 1 if i == 0 else 0),
            )
        for e in emails:
            from src.normalize import normalize_email
            await db.execute(
                """
                INSERT INTO contact_emails (contact_id, value, value_norm, label)
                VALUES (?, ?, ?, 'home')
                """,
                (cid, e, normalize_email(e)),
            )
        await db.commit()
        return cid


async def test_cluster_by_phone():
    uid = await _seed_user()
    a = await _add_contact(uid, "Ahmed H.", ["+20 100 1"])
    b = await _add_contact(uid, "Ahmed Hassan", ["20100-1"])  # same digits
    await _add_contact(uid, "Other", ["999"])

    async with connect() as db:
        clusters = await _build_clusters(db, uid)
    assert len(clusters) == 1
    assert clusters[0]["kind"] == "phone"
    assert set(clusters[0]["ids"]) == {a, b}


async def test_cluster_by_email():
    uid = await _seed_user(tg=101)
    a = await _add_contact(uid, "X", ["111"], ["a@b.com"])
    b = await _add_contact(uid, "Y", ["222"], ["A@B.COM"])  # same when normalized

    async with connect() as db:
        clusters = await _build_clusters(db, uid)
    kinds = [c["kind"] for c in clusters]
    assert "email" in kinds


async def test_merge_moves_phones_emails_circles_interactions():
    uid = await _seed_user(tg=102)
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)", (uid, "Friends")
        )
        circle_id = cur.lastrowid
        await db.commit()

    winner = await _add_contact(uid, "Ahmed", ["+20 100 1"], ["w@x.com"])
    loser = await _add_contact(uid, "Ahmed dup", ["20100-1", "+20 200 2"], ["l@x.com"])

    async with connect() as db:
        await db.execute(
            "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
            (loser, circle_id),
        )
        await db.execute(
            "INSERT INTO interactions (contact_id, kind) VALUES (?, 'talked')", (loser,)
        )
        await db.commit()

    await merge_duplicates(winner_id=winner, loser_ids=[loser], user_id=uid)

    async with connect() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE user_id = ?", (uid,)
        ) as cur:
            assert (await cur.fetchone())["n"] == 1
        async with db.execute(
            "SELECT value FROM contact_phones WHERE contact_id = ? ORDER BY value",
            (winner,),
        ) as cur:
            vals = [r["value"] for r in await cur.fetchall()]
        async with db.execute(
            "SELECT value FROM contact_emails WHERE contact_id = ? ORDER BY value",
            (winner,),
        ) as cur:
            emails = sorted(r["value"] for r in await cur.fetchall())
        async with db.execute(
            "SELECT circle_id FROM contact_circles WHERE contact_id = ?", (winner,)
        ) as cur:
            cids = [r["circle_id"] for r in await cur.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) AS n FROM interactions WHERE contact_id = ?", (winner,)
        ) as cur:
            interactions = (await cur.fetchone())["n"]

    # Winner keeps its phone, gets loser's extra phone; the conflicting duplicate
    # is skipped (UNIQUE on value_norm). So 2 distinct phones total.
    assert len(vals) == 2
    assert sorted(emails) == ["l@x.com", "w@x.com"]
    assert cids == [circle_id]
    assert interactions == 1


async def test_dismiss_makes_cluster_disappear():
    uid = await _seed_user(tg=103)
    a = await _add_contact(uid, "A", ["111"])
    b = await _add_contact(uid, "B", ["111"])

    assert (await count_duplicates_for_user(uid)) == 1
    await dismiss_duplicate(contact_ids=[a, b], user_id=uid)
    assert (await count_duplicates_for_user(uid)) == 0
