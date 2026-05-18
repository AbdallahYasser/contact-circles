"""vCard (.vcf) import for iPhone Contacts export.

Behavior:
- Skip vCards with no name or no phone.
- Extract every TEL and EMAIL (with iPhone TYPE label).
- Auto-merge: if any normalized phone/email already exists for the user,
  attach the new info (phones, emails, circle memberships) to that existing
  contact instead of creating a duplicate.
"""
import logging
from typing import Iterable

import vobject
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.auth import get_current_user_id
from src.db import connect
from src.normalize import (
    canonical_email_label,
    canonical_phone_label,
    normalize_email,
    normalize_phone,
)

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_VCARDS = 5000


def _tel_types(tel) -> list[str]:
    """vobject stores TYPE params on tel.params (dict-ish) or tel.type_paramlist."""
    types: list[str] = []
    try:
        for k, v in tel.params.items():
            if k.lower() == "type":
                if isinstance(v, list):
                    types.extend([str(x) for x in v])
                else:
                    types.append(str(v))
    except Exception:
        pass
    return types


def _extract_phones(vcard) -> list[tuple[str, str | None]]:
    """Return list of (raw_value, canonical_label) for every TEL entry."""
    if not hasattr(vcard, "tel_list"):
        return []
    out: list[tuple[str, str | None]] = []
    seen_norms: set[str] = set()
    for tel in vcard.tel_list:
        val = getattr(tel, "value", None)
        if not val:
            continue
        val = val.strip()
        norm = normalize_phone(val)
        if not norm or norm in seen_norms:
            continue
        seen_norms.add(norm)
        out.append((val, canonical_phone_label(_tel_types(tel))))
    return out


def _extract_emails(vcard) -> list[tuple[str, str | None]]:
    if not hasattr(vcard, "email_list"):
        return []
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for em in vcard.email_list:
        val = getattr(em, "value", None)
        if not val:
            continue
        val = val.strip()
        norm = normalize_email(val)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        types: list[str] = []
        try:
            for k, v in em.params.items():
                if k.lower() == "type":
                    types.extend(v if isinstance(v, list) else [str(v)])
        except Exception:
            pass
        out.append((val, canonical_email_label(types)))
    return out


def _extract_name(vcard) -> str | None:
    fn = getattr(vcard, "fn", None)
    if fn and fn.value and fn.value.strip():
        return fn.value.strip()
    n = getattr(vcard, "n", None)
    if n and n.value:
        parts = [getattr(n.value, "given", "") or "", getattr(n.value, "family", "") or ""]
        joined = " ".join(p for p in parts if p).strip()
        return joined or None
    return None


async def _find_existing_contact_id(
    db, user_id: int, phone_norms: list[str], email_norms: list[str]
) -> int | None:
    """Return the first contact owned by user_id that already has any of these
    normalized phones or emails."""
    if phone_norms:
        ph_q = (
            "SELECT cp.contact_id FROM contact_phones cp "
            "JOIN contacts c ON c.id = cp.contact_id "
            f"WHERE c.user_id = ? AND cp.value_norm IN ({','.join('?' * len(phone_norms))}) "
            "LIMIT 1"
        )
        async with db.execute(ph_q, (user_id, *phone_norms)) as cur:
            row = await cur.fetchone()
            if row:
                return int(row["contact_id"])
    if email_norms:
        em_q = (
            "SELECT ce.contact_id FROM contact_emails ce "
            "JOIN contacts c ON c.id = ce.contact_id "
            f"WHERE c.user_id = ? AND ce.value_norm IN ({','.join('?' * len(email_norms))}) "
            "LIMIT 1"
        )
        async with db.execute(em_q, (user_id, *email_norms)) as cur:
            row = await cur.fetchone()
            if row:
                return int(row["contact_id"])
    return None


@router.get("/import", response_class=HTMLResponse)
async def import_form(request: Request, user_id: int = Depends(get_current_user_id)):
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

    text = raw.decode("utf-8", errors="replace")

    imported = 0
    merged = 0
    skipped_no_phone = 0
    skipped_dupe_in_file = 0
    parse_errors = 0

    async with connect() as db:
        # Validate the user actually owns the circles they ticked.
        valid_circle_ids: list[int] = []
        if circle_ids:
            async with db.execute(
                f"SELECT id FROM circles WHERE user_id = ? "
                f"AND id IN ({','.join('?' * len(circle_ids))})",
                (user_id, *circle_ids),
            ) as cur:
                valid_circle_ids = [int(r["id"]) for r in await cur.fetchall()]

        # Tracks norms we've already imported in THIS file, so a vCard appearing
        # twice in the same export doesn't try to insert twice.
        within_file_phone_norms: set[str] = set()
        within_file_email_norms: set[str] = set()

        try:
            stream: Iterable = vobject.readComponents(text)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not parse vCard file: {e}")

        count = 0
        for vcard in stream:
            count += 1
            if count > MAX_VCARDS:
                logger.warning("Hit MAX_VCARDS=%d, stopping", MAX_VCARDS)
                break
            try:
                name = _extract_name(vcard)
                phones = _extract_phones(vcard)
                emails = _extract_emails(vcard)
                if not name or not phones:
                    skipped_no_phone += 1
                    continue

                phone_norms = [normalize_phone(v) for v, _ in phones]
                email_norms = [normalize_email(v) for v, _ in emails]

                # Skip if every phone we'd insert was already imported earlier in this file.
                if phone_norms and all(p in within_file_phone_norms for p in phone_norms):
                    skipped_dupe_in_file += 1
                    continue

                existing_id = await _find_existing_contact_id(
                    db, user_id, phone_norms, email_norms
                )

                if existing_id is None:
                    # New contact.
                    cur = await db.execute(
                        "INSERT INTO contacts (user_id, full_name, phone) VALUES (?, ?, ?)",
                        (user_id, name, phones[0][0]),
                    )
                    target_id = cur.lastrowid
                    for i, (val, lab) in enumerate(phones):
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO contact_phones
                                (contact_id, value, value_norm, label, is_primary)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (target_id, val, normalize_phone(val), lab, 1 if i == 0 else 0),
                        )
                    for val, lab in emails:
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO contact_emails
                                (contact_id, value, value_norm, label)
                            VALUES (?, ?, ?, ?)
                            """,
                            (target_id, val, normalize_email(val), lab),
                        )
                    imported += 1
                else:
                    # Merge into the existing contact.
                    target_id = existing_id
                    for val, lab in phones:
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO contact_phones
                                (contact_id, value, value_norm, label, is_primary)
                            VALUES (?, ?, ?, ?, 0)
                            """,
                            (target_id, val, normalize_phone(val), lab),
                        )
                    for val, lab in emails:
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO contact_emails
                                (contact_id, value, value_norm, label)
                            VALUES (?, ?, ?, ?)
                            """,
                            (target_id, val, normalize_email(val), lab),
                        )
                    merged += 1

                # Attach to the chosen circles (whether new or merged).
                for cid in valid_circle_ids:
                    await db.execute(
                        "INSERT OR IGNORE INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                        (target_id, cid),
                    )

                within_file_phone_norms.update(p for p in phone_norms if p)
                within_file_email_norms.update(e for e in email_norms if e)

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
                "merged": merged,
                "skipped_no_phone": skipped_no_phone,
                "skipped_dupe_in_file": skipped_dupe_in_file,
                "parse_errors": parse_errors,
            },
        },
    )
