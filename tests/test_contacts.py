"""Tests for the central feature: a contact can live in multiple circles."""
from src.db import connect


async def test_many_to_many_contact_circle_membership():
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (777, "U")
        )
        user_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)",
            (user_id, "School"),
        )
        school_id = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)",
            (user_id, "Close Friends"),
        )
        cf_id = cur.lastrowid

        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name) VALUES (?, ?)",
            (user_id, "Ahmed"),
        )
        ahmed = cur.lastrowid

        # The headline assertion: Ahmed belongs to both circles.
        for cid in (school_id, cf_id):
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (ahmed, cid),
            )
        await db.commit()

        async with db.execute(
            "SELECT COUNT(*) AS n FROM contact_circles WHERE contact_id = ?",
            (ahmed,),
        ) as c:
            row = await c.fetchone()
            assert row["n"] == 2

        # Re-inserting the same pair is a no-op (PK constraint).
        try:
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (ahmed, school_id),
            )
            await db.commit()
            duped = True
        except Exception:
            duped = False
        assert duped is False


async def test_cascade_delete_contact_removes_memberships():
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)", (1, "U")
        )
        uid = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO circles (user_id, name) VALUES (?, ?)", (uid, "X")
        )
        cid = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name) VALUES (?, ?)", (uid, "A")
        )
        aid = cur.lastrowid
        await db.execute(
            "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
            (aid, cid),
        )
        await db.execute(
            "INSERT INTO interactions (contact_id, kind) VALUES (?, 'talked')",
            (aid,),
        )
        await db.commit()

        await db.execute("DELETE FROM contacts WHERE id = ?", (aid,))
        await db.commit()

        async with db.execute(
            "SELECT COUNT(*) AS n FROM contact_circles WHERE contact_id = ?",
            (aid,),
        ) as c:
            assert (await c.fetchone())["n"] == 0
        async with db.execute(
            "SELECT COUNT(*) AS n FROM interactions WHERE contact_id = ?",
            (aid,),
        ) as c:
            assert (await c.fetchone())["n"] == 0
