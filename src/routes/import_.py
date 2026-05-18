"""vCard (.vcf) import — for iPhone Contacts export.

Skips entries without a phone or name. Dedupes against existing contacts
(per-user) by comparing digits-only phone numbers. Optionally assigns
imported contacts to one or more circles.
"""
import logging
import re
from typing import Iterable

import vobject
from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.auth import get_current_user_id
from src.db import connect

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_VCARDS = 5000


def _normalize_phone(raw: str | None) -> str:
    """Strip everything except digits. Used for dedupe only."""
    if not raw:
        return ""
    return re.sub(r"\D+", "", raw)


def _extract_first_phone(vcard) -> str | None:
    """Return the first phone in raw form (preserving + and formatting)."""
    if not hasattr(vcard, "tel_list"):
        return None
    for tel in vcard.tel_list:
        val = getattr(tel, "value", None)
        if val and _normalize_phone(val):
            return val.strip()
    return None


def _extract_name(vcard) -> str | None:
    fn = getattr(vcard, "fn", None)
    if fn and fn.value and fn.value.strip():
        return fn.value.strip()
    n = getattr(vcard, "n", None)
    if n and n.value:
        # vobject parses N into a structured value with .given/.family etc.
        parts = [getattr(n.value, "given", "") or "", getattr(n.value, "family", "") or ""]
        joined = " ".join(p for p in parts if p).strip()
        return joined or None
    return None


@router.get("/import", response_class=HTMLResponse)
async def import_form(
    request: Request, user_id: int = Depends(get_current_user_id)
):
    async with connect() as db:
        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]
    return templates.TemplateResponse(
        "import.html",
        {"request": request, "circles": circles, "result": None},
    )


@router.post("/import", response_class=HTMLResponse)
async def import_post(
    request: Request,
    file: UploadFile = File(...),
    circle_ids: list[int] = Form(default=[]),
    user_id: int = Depends(get_current_user_id),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot decode file: {e}")

    imported = 0
    skipped_no_phone = 0
    skipped_dupe = 0
    parse_errors = 0

    async with connect() as db:
        # Existing phones (per-user) for dedupe.
        async with db.execute(
            "SELECT phone FROM contacts WHERE user_id = ? AND phone IS NOT NULL",
            (user_id,),
        ) as cur:
            existing = {_normalize_phone(r["phone"]) for r in await cur.fetchall()}
            existing.discard("")

        # Sanity-check circle ownership once.
        valid_circle_ids: list[int] = []
        if circle_ids:
            async with db.execute(
                f"SELECT id FROM circles WHERE user_id = ? "
                f"AND id IN ({','.join('?' * len(circle_ids))})",
                (user_id, *circle_ids),
            ) as cur:
                valid_circle_ids = [int(r["id"]) for r in await cur.fetchall()]

        count = 0
        try:
            stream: Iterable = vobject.readComponents(text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not parse vCard file: {e}")

        for vcard in stream:
            count += 1
            if count > MAX_VCARDS:
                logger.warning("Hit MAX_VCARDS=%d limit, stopping", MAX_VCARDS)
                break
            try:
                name = _extract_name(vcard)
                phone = _extract_first_phone(vcard)
                if not name or not phone:
                    skipped_no_phone += 1
                    continue
                norm = _normalize_phone(phone)
                if norm in existing:
                    skipped_dupe += 1
                    continue
                existing.add(norm)

                cur = await db.execute(
                    "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
                    (user_id, name, phone),
                )
                contact_id = cur.lastrowid
                for cid in valid_circle_ids:
                    await db.execute(
                        "INSERT OR IGNORE INTO contact_circles "
                        "(contact_id, circle_id) VALUES (?, ?)",
                        (contact_id, cid),
                    )
                imported += 1
            except Exception as e:
                parse_errors += 1
                logger.debug("vCard parse error: %s", e)
                continue

        await db.commit()

    async with connect() as db:
        async with db.execute(
            "SELECT id, name, color FROM circles WHERE user_id = ? ORDER BY name",
            (user_id,),
        ) as cur:
            circles = [dict(r) for r in await cur.fetchall()]

    return templates.TemplateResponse(
        "import.html",
        {
            "request": request,
            "circles": circles,
            "result": {
                "imported": imported,
                "skipped_no_phone": skipped_no_phone,
                "skipped_dupe": skipped_dupe,
                "parse_errors": parse_errors,
            },
        },
    )
