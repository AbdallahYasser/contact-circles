"""Tests for the concentric Dunbar viz: tier resolution + extra-circle halos."""
from src.db import connect
from src.routes.dashboard import _build_viz


async def _seed():
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO users (telegram_id, first_name) VALUES (?, ?)",
            (12345, "Tester"),
        )
        uid = cur.lastrowid
        # Two circles, different cadences.
        cur = await db.execute(
            "INSERT INTO circles (user_id, name, color, default_cadence_days) VALUES (?, ?, ?, ?)",
            (uid, "Close Friends", "#ef4444", 7),
        )
        close = cur.lastrowid
        cur = await db.execute(
            "INSERT INTO circles (user_id, name, color, default_cadence_days) VALUES (?, ?, ?, ?)",
            (uid, "School", "#0ea5e9", 90),
        )
        school = cur.lastrowid
        await db.commit()
        return uid, close, school


async def test_innermost_circle_wins_for_contact_in_two():
    uid, close, school = await _seed()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "Ahmed", "0100"),
        )
        ahmed = cur.lastrowid
        for cid in (close, school):
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (ahmed, cid),
            )
        await db.commit()

    viz = await _build_viz(uid)
    rings = viz["rings"]
    # Inner ring is Close Friends (cadence 7), outer is School (cadence 90).
    assert rings[0]["name"] == "Close Friends"
    assert rings[1]["name"] == "School"
    # Ahmed is on the inner ring, with School's color as an extra halo.
    inner_names = [c["name"] for c in rings[0]["contacts"]]
    outer_names = [c["name"] for c in rings[1]["contacts"]]
    assert "Ahmed" in inner_names
    assert "Ahmed" not in outer_names
    ahmed_node = next(c for c in rings[0]["contacts"] if c["name"] == "Ahmed")
    assert ahmed_node["extra_colors"] == ["#0ea5e9"]


async def test_contact_in_one_circle_has_no_extras():
    uid, close, school = await _seed()
    async with connect() as db:
        cur = await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "Sara", "0200"),
        )
        sara = cur.lastrowid
        await db.execute(
            "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
            (sara, school),
        )
        await db.commit()

    viz = await _build_viz(uid)
    sara_node = next(
        c for ring in viz["rings"] for c in ring["contacts"] if c["name"] == "Sara"
    )
    assert sara_node["extra_colors"] == []


async def test_contact_with_no_circle_goes_to_unassigned_ring():
    uid, _, _ = await _seed()
    async with connect() as db:
        await db.execute(
            "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
            (uid, "Lone", "0300"),
        )
        await db.commit()

    viz = await _build_viz(uid)
    assert viz["rings"][-1]["name"] == "Unassigned"
    assert [c["name"] for c in viz["rings"][-1]["contacts"]] == ["Lone"]


async def test_dot_positions_evenly_distributed():
    """Two contacts on one ring should sit at antipodal points."""
    uid, close, _ = await _seed()
    async with connect() as db:
        for name, phone in [("A", "1"), ("B", "2")]:
            cur = await db.execute(
                "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
                (uid, name, phone),
            )
            await db.execute(
                "INSERT INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (cur.lastrowid, close),
            )
        await db.commit()

    viz = await _build_viz(uid)
    pts = [(c["x"], c["y"]) for c in viz["rings"][0]["contacts"]]
    assert len(pts) == 2
    # B is 180 degrees from A.
    assert abs(pts[0][0] + pts[1][0]) < 0.01
    assert abs(pts[0][1] + pts[1][1]) < 0.01
