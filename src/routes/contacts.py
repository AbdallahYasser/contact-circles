"""Contact CRUD + circle membership toggling."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from src.auth import get_current_user_id
from src.db import connect
from src.normalize import normalize_email, normalize_phone

router = APIRouter(prefix="/contacts")
templates = Jinja2Templates(directory="src/templates")

PHONE_LABELS = ("mobile", "home", "work", "other")
EMAIL_LABELS = ("home", "work", "other")


async def _phones_for_contact(db, contact_id: int) -> list[dict]:
    async with db.execute(
        """
        SELECT id, value, label, is_primary
        FROM contact_phones WHERE contact_id = ?
        ORDER BY is_primary DESC, id ASC
        """,
        (contact_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def _emails_for_contact(db, contact_id: int) -> list[dict]:
    async with db.execute(
        """
        SELECT id, value, label
        FROM contact_emails WHERE contact_id = ?
        ORDER BY id ASC
        """,
        (contact_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


def _zip_pairs(values: list[str], labels: list[str], allowed: tuple) -> list[tuple[str, str | None]]:
    """Pair up parallel form arrays, drop empty values, normalize labels."""
    pairs: list[tuple[str, str | None]] = []
    for i, raw in enumerate(values):
        if not raw or not raw.strip():
            continue
        lab = labels[i].strip().lower() if i < len(labels) and labels[i] else None
        if lab not in allowed:
            lab = None
        pairs.append((raw.strip(), lab))
    return pairs


async def _replace_phones(db, contact_id: int, pairs: list[tuple[str, str | None]]) -> str:
    """Wipe existing phones and rewrite from `pairs`. Returns the primary phone value."""
    await db.execute("DELETE FROM contact_phones WHERE contact_id = ?", (contact_id,))
    primary = ""
    seen_norms: set[str] = set()
    for i, (val, lab) in enumerate(pairs):
        norm = normalize_phone(val)
        if not norm or norm in seen_norms:
            continue
        seen_norms.add(norm)
        await db.execute(
            """
            INSERT INTO contact_phones (contact_id, value, value_norm, label, is_primary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (contact_id, val, norm, lab, 1 if not primary else 0),
        )
        if not primary:
            primary = val
    return primary


async def _replace_emails(db, contact_id: int, pairs: list[tuple[str, str | None]]) -> None:
    await db.execute("DELETE FROM contact_emails WHERE contact_id = ?", (contact_id,))
    seen: set[str] = set()
    for val, lab in pairs:
        norm = normalize_email(val)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        await db.execute(
            """
            INSERT INTO contact_emails (contact_id, value, value_norm, label)
            VALUES (?, ?, ?, ?)
            """,
            (contact_id, val, norm, lab),
        )


async def _user_owns_contact(db, user_id: int, contact_id: int) -> bool:
    async with db.execute(
        "SELECT 1 FROM contacts WHERE id = ? AND user_id = ?",
        (contact_id, user_id),
    ) as cur:
        return (await cur.fetchone()) is not None


async def _circles_for_contact(db, contact_id: int) -> list[dict]:
    async with db.execute(
        """
        SELECT c.id, c.name, c.color
        FROM circles c
        JOIN contact_circles cc ON cc.circle_id = c.id
        WHERE cc.contact_id = ?
        ORDER BY c.name
        """,
        (contact_id,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]


@router.get("", response_class=HTMLResponse)
async def list_contacts(
    request: Request,
    circle: int | None = None,
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        if circle is None:
            sql = """
                SELECT id, full_name, nickname, last_contacted_at
                FROM contacts WHERE user_id = ?
                ORDER BY full_name
            """
            params: tuple = (user_id,)
        else:
            sql = """
                SELECT c.id, c.full_name, c.nickname, c.last_contacted_at
                FROM contacts c
                JOIN contact_circles cc ON cc.contact_id = c.id
                WHERE c.user_id = ? AND cc.circle_id = ?
                ORDER BY c.full_name
            """
            params = (user_id, circle)
        async with db.execute(sql, params) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

        for r in rows:
            r["circles"] = await _circles_for_contact(db, r["id"])

        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]

    return templates.TemplateResponse(
        "contact_list.html",
        {
            "request": request,
            "contacts": rows,
            "circles": circles,
            "selected_circle": circle,
        },
    )


@router.get("/new", response_class=HTMLResponse)
async def new_contact_form(
    request: Request, user_id: int = Depends(get_current_user_id)
):
    async with connect() as db:
        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        "contact_form.html",
        {
            "request": request,
            "contact": None,
            "selected_ids": set(),
            "circles": circles,
            "phones": [],
            "emails": [],
            "phone_labels": PHONE_LABELS,
            "email_labels": EMAIL_LABELS,
        },
    )


@router.post("")
async def create_contact(
    full_name: str = Form(...),
    nickname: str = Form(""),
    phone_value: list[str] = Form(default=[]),
    phone_label: list[str] = Form(default=[]),
    email_value: list[str] = Form(default=[]),
    email_label: list[str] = Form(default=[]),
    telegram_handle: str = Form(""),
    birthday: str = Form(""),
    notes: str = Form(""),
    circle_ids: list[int] = Form(default=[]),
    user_id: int = Depends(get_current_user_id),
):
    if not full_name.strip():
        raise HTTPException(status_code=400, detail="full_name required")

    phone_pairs = _zip_pairs(phone_value, phone_label, PHONE_LABELS)
    email_pairs = _zip_pairs(email_value, email_label, EMAIL_LABELS)
    if not phone_pairs:
        raise HTTPException(status_code=400, detail="at least one phone is required")

    primary = phone_pairs[0][0]

    async with connect() as db:
        cur = await db.execute(
            """
            INSERT INTO contacts (user_id, full_name, nickname, phone,
                                  telegram_handle, birthday, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, full_name.strip(), nickname or None, primary,
             telegram_handle or None, birthday or None, notes or None),
        )
        contact_id = cur.lastrowid
        actual_primary = await _replace_phones(db, contact_id, phone_pairs)
        await _replace_emails(db, contact_id, email_pairs)
        if actual_primary and actual_primary != primary:
            await db.execute(
                "UPDATE contacts SET phone = ? WHERE id = ?",
                (actual_primary, contact_id),
            )
        for cid in circle_ids:
            async with db.execute(
                "SELECT 1 FROM circles WHERE id = ? AND user_id = ?",
                (cid, user_id),
            ) as c:
                if not await c.fetchone():
                    continue
            await db.execute(
                "INSERT OR IGNORE INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (contact_id, cid),
            )
        await db.commit()
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=303)


@router.post("/cleanup/no-phone")
async def cleanup_no_phone(
    request: Request,
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        cur = await db.execute(
            """
            DELETE FROM contacts
            WHERE user_id = ?
              AND (phone IS NULL OR TRIM(phone) = '')
            """,
            (user_id,),
        )
        await db.commit()
        deleted = cur.rowcount or 0

    if request.headers.get("HX-Request"):
        return Response(
            status_code=200,
            content=(
                f'<span class="text-green-600 text-sm">'
                f"🧹 Removed {deleted} contact{'s' if deleted != 1 else ''} without a phone.</span>"
            ),
            media_type="text/html",
        )
    return RedirectResponse(url="/", status_code=303)


@router.get("/{contact_id}", response_class=HTMLResponse)
async def contact_detail(
    request: Request,
    contact_id: int,
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        if not await _user_owns_contact(db, user_id, contact_id):
            raise HTTPException(status_code=404, detail="Not found")
        async with db.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ) as cur:
            contact = dict(await cur.fetchone())

        contact["circles"] = await _circles_for_contact(db, contact_id)
        contact["phones"] = await _phones_for_contact(db, contact_id)
        contact["emails"] = await _emails_for_contact(db, contact_id)

        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            all_circles = [dict(r) for r in await cur.fetchall()]
        selected_ids = {c["id"] for c in contact["circles"]}

        async with db.execute(
            """
            SELECT id, kind, note, occurred_at FROM interactions
            WHERE contact_id = ? ORDER BY occurred_at DESC LIMIT 50
            """,
            (contact_id,),
        ) as cur:
            interactions = [dict(r) for r in await cur.fetchall()]

    return templates.TemplateResponse(
        "contact_detail.html",
        {
            "request": request,
            "contact": contact,
            "all_circles": all_circles,
            "selected_ids": selected_ids,
            "interactions": interactions,
        },
    )


@router.get("/{contact_id}/edit", response_class=HTMLResponse)
async def edit_contact_form(
    request: Request,
    contact_id: int,
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        if not await _user_owns_contact(db, user_id, contact_id):
            raise HTTPException(status_code=404)
        async with db.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ) as cur:
            contact = dict(await cur.fetchone())
        contact["circles"] = await _circles_for_contact(db, contact_id)
        phones = await _phones_for_contact(db, contact_id)
        emails = await _emails_for_contact(db, contact_id)
        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            all_circles = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        "contact_form.html",
        {
            "request": request,
            "contact": contact,
            "selected_ids": {c["id"] for c in contact["circles"]},
            "circles": all_circles,
            "phones": phones,
            "emails": emails,
            "phone_labels": PHONE_LABELS,
            "email_labels": EMAIL_LABELS,
        },
    )


@router.post("/{contact_id}/edit")
async def update_contact(
    contact_id: int,
    full_name: str = Form(...),
    nickname: str = Form(""),
    phone_value: list[str] = Form(default=[]),
    phone_label: list[str] = Form(default=[]),
    email_value: list[str] = Form(default=[]),
    email_label: list[str] = Form(default=[]),
    telegram_handle: str = Form(""),
    birthday: str = Form(""),
    notes: str = Form(""),
    circle_ids: list[int] = Form(default=[]),
    user_id: int = Depends(get_current_user_id),
):
    phone_pairs = _zip_pairs(phone_value, phone_label, PHONE_LABELS)
    email_pairs = _zip_pairs(email_value, email_label, EMAIL_LABELS)
    if not phone_pairs:
        raise HTTPException(status_code=400, detail="at least one phone is required")

    primary = phone_pairs[0][0]

    async with connect() as db:
        if not await _user_owns_contact(db, user_id, contact_id):
            raise HTTPException(status_code=404)
        await db.execute(
            """
            UPDATE contacts
            SET full_name = ?, nickname = ?, phone = ?, telegram_handle = ?,
                birthday = ?, notes = ?
            WHERE id = ?
            """,
            (full_name.strip(), nickname or None, primary,
             telegram_handle or None, birthday or None, notes or None,
             contact_id),
        )
        actual_primary = await _replace_phones(db, contact_id, phone_pairs)
        await _replace_emails(db, contact_id, email_pairs)
        if actual_primary and actual_primary != primary:
            await db.execute(
                "UPDATE contacts SET phone = ? WHERE id = ?",
                (actual_primary, contact_id),
            )
        await db.execute(
            "DELETE FROM contact_circles WHERE contact_id = ?", (contact_id,)
        )
        for cid in circle_ids:
            async with db.execute(
                "SELECT 1 FROM circles WHERE id = ? AND user_id = ?",
                (cid, user_id),
            ) as c:
                if not await c.fetchone():
                    continue
            await db.execute(
                "INSERT OR IGNORE INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                (contact_id, cid),
            )
        await db.commit()
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=303)


@router.post("/{contact_id}/delete")
async def delete_contact(
    contact_id: int, user_id: int = Depends(get_current_user_id)
):
    async with connect() as db:
        if not await _user_owns_contact(db, user_id, contact_id):
            raise HTTPException(status_code=404)
        await db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        await db.commit()
    return RedirectResponse(url="/contacts", status_code=303)


@router.post("/{contact_id}/talked")
async def log_talked(
    request: Request,
    contact_id: int,
    user_id: int = Depends(get_current_user_id),
):
    async with connect() as db:
        if not await _user_owns_contact(db, user_id, contact_id):
            raise HTTPException(status_code=404)
        await db.execute(
            "INSERT INTO interactions (contact_id, kind) VALUES (?, 'talked')",
            (contact_id,),
        )
        await db.execute(
            "UPDATE contacts SET last_contacted_at = datetime('now') WHERE id = ?",
            (contact_id,),
        )
        await db.commit()
    # HTMX-friendly: if HX-Request, return an empty 204 and let client refresh
    if request.headers.get("HX-Request"):
        return Response(
            status_code=200,
            content='<span class="text-green-600">✅ Logged just now</span>',
            media_type="text/html",
        )
    return RedirectResponse(url=f"/contacts/{contact_id}", status_code=303)
