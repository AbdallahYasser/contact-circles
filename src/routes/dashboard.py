"""Dashboard: concentric Dunbar rings viz hero + overdue list."""
import math

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import auth
from src.reminders import overdue_contacts_for_user
from src.routes.duplicates import count_duplicates_for_user
from src.db import connect

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


async def _build_viz(user_id: int) -> dict:
    """Place each contact on a single ring (their innermost circle by cadence).
    Contacts in extra circles get those circles' colors as 'extra_colors'.
    Contacts with no circle go to a synthetic 'Outer' ring at the back.
    """
    async with connect() as db:
        async with db.execute(
            """
            SELECT id, name, color, default_cadence_days
            FROM circles WHERE user_id = ?
            ORDER BY default_cadence_days ASC, id ASC
            """,
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            """
            SELECT c.id, c.full_name, c.last_contacted_at,
                   GROUP_CONCAT(cl.id) AS circle_id_csv
            FROM contacts c
            LEFT JOIN contact_circles cc ON cc.contact_id = c.id
            LEFT JOIN circles cl ON cl.id = cc.circle_id
            WHERE c.user_id = ?
            GROUP BY c.id
            """,
            (user_id,),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    # Sort circles by cadence ASC so innermost (closest) is index 0.
    by_id = {c["id"]: c for c in circles}
    order = [c["id"] for c in circles]
    order_idx = {cid: i for i, cid in enumerate(order)}

    # Build rings (one per circle).
    rings = [
        {"id": c["id"], "name": c["name"], "color": c["color"],
         "cadence": c["default_cadence_days"], "contacts": []}
        for c in circles
    ]
    outer_ring = {"id": None, "name": "Unassigned", "color": "#cbd5e1",
                  "cadence": None, "contacts": []}

    for r in rows:
        ids_csv = r["circle_id_csv"] or ""
        cids = [int(x) for x in ids_csv.split(",") if x]
        if not cids:
            outer_ring["contacts"].append({
                "id": r["id"], "name": r["full_name"], "extra_colors": [],
            })
            continue
        cids.sort(key=lambda i: order_idx.get(i, 999))
        primary = cids[0]
        extras = [by_id[i]["color"] for i in cids[1:] if i in by_id]
        target_ring = next(rg for rg in rings if rg["id"] == primary)
        target_ring["contacts"].append({
            "id": r["id"], "name": r["full_name"], "extra_colors": extras,
        })

    if outer_ring["contacts"]:
        rings.append(outer_ring)

    # Geometry: precompute (x, y, r_ring) for each dot. Inline SVG, no JS math.
    viewbox_half = 220
    base_r = 60
    step = 0
    if rings:
        step = max(28, (viewbox_half - base_r - 18) // max(len(rings), 1))

    enriched = []
    for i, ring in enumerate(rings):
        ring_r = base_r + i * step
        n = max(len(ring["contacts"]), 1)
        # Start at top (-90deg) so first contact is at 12 o'clock.
        contacts_xy = []
        for j, c in enumerate(ring["contacts"]):
            theta = -math.pi / 2 + 2 * math.pi * j / n
            x = ring_r * math.cos(theta)
            y = ring_r * math.sin(theta)
            contacts_xy.append({**c, "x": round(x, 2), "y": round(y, 2)})
        enriched.append({**ring, "ring_r": ring_r, "contacts": contacts_xy})

    return {
        "rings": enriched,
        "viewbox_half": viewbox_half,
        "has_data": bool(rows),
    }


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: str | None = Cookie(default=None)):
    if not session:
        return RedirectResponse(url="/login", status_code=302)
    try:
        user_id = auth.decode_session_token(session)
    except Exception:
        return RedirectResponse(url="/login", status_code=302)

    overdue = await overdue_contacts_for_user(user_id, limit=20)
    viz = await _build_viz(user_id)
    dup_count = await count_duplicates_for_user(user_id)

    async with connect() as db:
        async with db.execute(
            """
            SELECT c.id, c.name, c.color, c.default_cadence_days,
                   COUNT(cc.contact_id) AS member_count
            FROM circles c
            LEFT JOIN contact_circles cc ON cc.circle_id = c.id
            WHERE c.user_id = ?
            GROUP BY c.id
            ORDER BY c.default_cadence_days ASC, c.name ASC
            """,
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]

        async with db.execute(
            "SELECT COUNT(*) AS n FROM contacts WHERE user_id = ?", (user_id,)
        ) as cur:
            total_contacts = (await cur.fetchone())["n"]

        async with db.execute(
            """
            SELECT COUNT(*) AS n FROM contacts
            WHERE user_id = ? AND (phone IS NULL OR TRIM(phone) = '')
            """,
            (user_id,),
        ) as cur:
            phoneless_count = (await cur.fetchone())["n"]

        async with db.execute(
            "SELECT first_name, telegram_username FROM users WHERE id = ?",
            (user_id,),
        ) as cur:
            me = dict(await cur.fetchone())

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "overdue": overdue,
            "circles": circles,
            "total_contacts": total_contacts,
            "phoneless_count": phoneless_count,
            "dup_count": dup_count,
            "me": me,
            "viz": viz,
        },
    )
