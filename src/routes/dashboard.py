"""Dashboard: overdue contacts at top + circles overview."""
from fastapi import APIRouter, Cookie, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import auth
from src.reminders import overdue_contacts_for_user
from src.db import connect

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: str | None = Cookie(default=None)):
    if not session:
        return RedirectResponse(url="/login", status_code=302)
    try:
        user_id = auth.decode_session_token(session)
    except Exception:
        return RedirectResponse(url="/login", status_code=302)

    overdue = await overdue_contacts_for_user(user_id, limit=20)

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
            "me": me,
        },
    )
