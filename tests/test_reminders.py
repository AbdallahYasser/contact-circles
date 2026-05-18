"""Tests for the reminder sweep math — the headline feature.

The key invariant: a contact's effective cadence is the MIN cadence
across the circles it belongs to.
"""
from src.db import connect
from src.reminders import overdue_contacts_for_user


async def _setup_user_and_data() -> int:
    """Create one user, two circles with different cadences, one contact
    in both, return user_id."""
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (10001, "Tester"),
        )
        user_id = cur.lastrowid
        # School: cadence 90 days. Close Friends: cadence 7 days.
        await db.execute(
            "INSERT INTO circles (user_id, name, default_cadence_days) VALUES (?, ?, ?)",
            (user_id, "School", 90),
        )
        await db.execute(
            "INSERT INTO circles (user_id, name, default_cadence_days) VALUES (?, ?, ?)",
            (user_id, "Close Friends", 7),
        )
        await db.commit()
        return user_id


async def test_contact_in_multiple_circles_uses_min_cadence():
    user_id = await _setup_user_and_data()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, last_contacted_at) "
            "VALUES (?, ?, datetime('now', '-10 days'))",
            (user_id, "Multi"),
        )
        c_id = cur.lastrowid
        # Put in both circles.
        async with db.execute(
            "SELECT id FROM circles WHERE user_id = ?", (user_id,)
        ) as cc:
            ids = [r["id"] for r in await cc.fetchall()]
        for cid in ids:
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (c_id, cid),
            )
        await db.commit()

    overdue = await overdue_contacts_for_user(user_id)
    assert len(overdue) == 1
    # 10 days since last contact; min cadence is 7 → overdue.
    assert overdue[0]["cadence_days"] == 7
    assert overdue[0]["days_since"] >= 9  # allow some clock jitter


async def test_contact_not_overdue_is_omitted():
    user_id = await _setup_user_and_data()
    async with connect() as db:
        await db.execute(
            "INSERT INTO contacts (user_id, full_name, last_contacted_at) "
            "VALUES (?, ?, datetime('now', '-1 days'))",
            (user_id, "Just talked"),
        )
        await db.commit()

    overdue = await overdue_contacts_for_user(user_id)
    assert overdue == []


async def test_contact_with_no_circle_uses_fallback_cadence():
    user_id = await _setup_user_and_data()
    async with connect() as db:
        await db.execute(
            "INSERT INTO contacts (user_id, full_name, last_contacted_at) "
            "VALUES (?, ?, datetime('now', '-100 days'))",
            (user_id, "Orphan"),
        )
        await db.commit()

    overdue = await overdue_contacts_for_user(user_id)
    assert len(overdue) == 1
    assert overdue[0]["cadence_days"] == 90  # FALLBACK_CADENCE_DAYS


async def test_overdue_ranked_by_ratio():
    user_id = await _setup_user_and_data()
    async with connect() as db:
        # Both in Close Friends (cadence 7).
        async with db.execute(
            "SELECT id FROM circles WHERE user_id = ? AND name = 'Close Friends'",
            (user_id,),
        ) as cc:
            cf_id = (await cc.fetchone())["id"]

        for name, days in [("Recent", 10), ("Ancient", 60)]:
            cur = await db.execute(
                "INSERT INTO contacts (user_id, full_name, last_contacted_at) "
                "VALUES (?, ?, datetime('now', ?))",
                (user_id, name, f"-{days} days"),
            )
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (cur.lastrowid, cf_id),
            )
        await db.commit()

    overdue = await overdue_contacts_for_user(user_id)
    assert [c["full_name"] for c in overdue] == ["Ancient", "Recent"]
