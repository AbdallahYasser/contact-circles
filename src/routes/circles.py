"""Circle CRUD."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.auth import get_current_user_id
from src.db import connect

router = APIRouter(prefix="/circles")
templates = Jinja2Templates(directory="src/templates")


@router.get("", response_class=HTMLResponse)
async def list_circles(
    request: Request, user_id: int = Depends(get_current_user_id)
):
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
    return templates.TemplateResponse(
        "circles.html", {"request": request, "circles": circles}
    )


@router.post("")
async def create_circle(
    name: str = Form(...),
    color: str = Form("#6366f1"),
    default_cadence_days: int = Form(30),
    user_id: int = Depends(get_current_user_id),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="name required")
    if default_cadence_days < 1:
        raise HTTPException(status_code=400, detail="cadence must be >= 1")
    async with connect() as db:
        try:
            await db.execute(
                """
                INSERT INTO circles (user_id, name, color, default_cadence_days)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, name.strip(), color, default_cadence_days),
            )
            await db.commit()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not create: {e}")
    return RedirectResponse(url="/circles", status_code=303)


@router.post("/{circle_id}/edit")
async def update_circle(
    circle_id: int,
    name: str = Form(...),
    color: str = Form("#6366f1"),
    default_cadence_days: int = Form(30),
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        async with db.execute(
            "SELECT 1 FROM circles WHERE id = ? AND user_id = ?",
            (circle_id, user_id),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404)
        await db.execute(
            """
            UPDATE circles SET name = ?, color = ?, default_cadence_days = ?
            WHERE id = ?
            """,
            (name.strip(), color, default_cadence_days, circle_id),
        )
        await db.commit()
    return RedirectResponse(url="/circles", status_code=303)


@router.post("/{circle_id}/delete")
async def delete_circle(
    circle_id: int, user_id: int = Depends(get_current_user_id)
):
    async with connect() as db:
        async with db.execute(
            "SELECT 1 FROM circles WHERE id = ? AND user_id = ?",
            (circle_id, user_id),
        ) as cur:
            if not await cur.fetchone():
                raise HTTPException(status_code=404)
        await db.execute("DELETE FROM circles WHERE id = ?", (circle_id,))
        await db.commit()
    return RedirectResponse(url="/circles", status_code=303)
