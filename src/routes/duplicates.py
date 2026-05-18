"""Duplicate-contact detection + merging.

Clusters are derived from two SQL queries:
  - contacts sharing a normalized phone
  - contacts sharing a normalized email

Pairs the user has explicitly dismissed are skipped.
"""
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.auth import get_current_user_id
from src.db import connect

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


async def _dismissed_pairs(db, user_id: int) -> set[tuple[int, int]]:
    async with db.execute(
        "SELECT contact_a_id, contact_b_id FROM dismissed_duplicates WHERE user_id = ?",
        (user_id,),
    ) as cur:
        return {(r["contact_a_id"], r["contact_b_id"]) for r in await cur.fetchall()}


async def _build_clusters(db, user_id: int) -> list[dict]:
    """Return a list of clusters (>=2 contacts each)."""
    dismissed = await _dismissed_pairs(db, user_id)

    async def _by(table: str, kind: str) -> list[dict]:
        sql = f"""
            SELECT t.value_norm, GROUP_CONCAT(DISTINCT c.id) AS ids
            FROM {table} t JOIN contacts c ON c.id = t.contact_id
            WHERE c.user_id = ?
            GROUP BY t.value_norm
            HAVING COUNT(DISTINCT c.id) > 1
        """
        async with db.execute(sql, (user_id,)) as cur:
            raw = await cur.fetchall()
        out = []
        for r in raw:
            ids = sorted({int(x) for x in r["ids"].split(",")})
            # Skip if every pair within this cluster has been dismissed.
            still_pairs = [
                (a, b) for a in ids for b in ids if a < b and (a, b) not in dismissed
            ]
            if not still_pairs:
                continue
            out.append({"value_norm": r["value_norm"], "ids": ids, "kind": kind})
        return out

    clusters = await _by("contact_phones", "phone") + await _by("contact_emails", "email")
    return clusters


async def _enrich_cluster(db, cluster: dict) -> dict:
    ids = cluster["ids"]
    ph = f"({','.join('?' * len(ids))})"
    async with db.execute(
        f"SELECT id, full_name, nickname, phone, created_at FROM contacts WHERE id IN {ph}",
        ids,
    ) as cur:
        rows = {r["id"]: dict(r) for r in await cur.fetchall()}

    async with db.execute(
        f"SELECT contact_id, value, label FROM contact_phones WHERE contact_id IN {ph}",
        ids,
    ) as cur:
        phones_by_cid = defaultdict(list)
        for r in await cur.fetchall():
            phones_by_cid[r["contact_id"]].append(dict(r))

    async with db.execute(
        f"SELECT contact_id, value, label FROM contact_emails WHERE contact_id IN {ph}",
        ids,
    ) as cur:
        emails_by_cid = defaultdict(list)
        for r in await cur.fetchall():
            emails_by_cid[r["contact_id"]].append(dict(r))

    async with db.execute(
        f"""
        SELECT cc.contact_id, c.name, c.color
        FROM contact_circles cc JOIN circles c ON c.id = cc.circle_id
        WHERE cc.contact_id IN {ph}
        """,
        ids,
    ) as cur:
        circles_by_cid = defaultdict(list)
        for r in await cur.fetchall():
            circles_by_cid[r["contact_id"]].append(dict(r))

    candidates = []
    for cid in ids:
        if cid not in rows:
            continue
        c = rows[cid]
        candidates.append({
            **c,
            "phones": phones_by_cid.get(cid, []),
            "emails": emails_by_cid.get(cid, []),
            "circles": circles_by_cid.get(cid, []),
        })
    return {**cluster, "candidates": candidates}


@router.get("/duplicates", response_class=HTMLResponse)
async def list_duplicates(
    request: Request, user_id: int = Depends(get_current_user_id)
):
    async with connect() as db:
        raw = await _build_clusters(db, user_id)
        enriched = [await _enrich_cluster(db, c) for c in raw]
    return templates.TemplateResponse(
        "duplicates.html", {"request": request, "clusters": enriched}
    )


async def count_duplicates_for_user(user_id: int) -> int:
    """Used by dashboard to show the banner."""
    async with connect() as db:
        clusters = await _build_clusters(db, user_id)
        return len(clusters)


async def _user_owns(db, user_id: int, ids: list[int]) -> bool:
    if not ids:
        return False
    placeholders = ",".join("?" * len(ids))
    async with db.execute(
        f"SELECT COUNT(*) AS n FROM contacts WHERE user_id = ? AND id IN ({placeholders})",
        (user_id, *ids),
    ) as cur:
        row = await cur.fetchone()
    return int(row["n"]) == len(ids)


@router.post("/duplicates/merge")
async def merge_duplicates(
    winner_id: int = Form(...),
    loser_ids: list[int] = Form(default=[]),
    user_id: int = Depends(get_current_user_id),
):
    if winner_id in loser_ids:
        raise HTTPException(status_code=400, detail="winner cannot be a loser")
    if not loser_ids:
        raise HTTPException(status_code=400, detail="nothing to merge")

    async with connect() as db:
        if not await _user_owns(db, user_id, [winner_id, *loser_ids]):
            raise HTTPException(status_code=404, detail="not your contacts")

        for loser in loser_ids:
            # Phones: copy what doesn't conflict, then delete remaining from loser.
            async with db.execute(
                "SELECT value, value_norm, label FROM contact_phones WHERE contact_id = ?",
                (loser,),
            ) as cur:
                for r in await cur.fetchall():
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO contact_phones
                            (contact_id, value, value_norm, label, is_primary)
                        VALUES (?, ?, ?, ?, 0)
                        """,
                        (winner_id, r["value"], r["value_norm"], r["label"]),
                    )
            await db.execute("DELETE FROM contact_phones WHERE contact_id = ?", (loser,))

            async with db.execute(
                "SELECT value, value_norm, label FROM contact_emails WHERE contact_id = ?",
                (loser,),
            ) as cur:
                for r in await cur.fetchall():
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO contact_emails
                            (contact_id, value, value_norm, label)
                        VALUES (?, ?, ?, ?)
                        """,
                        (winner_id, r["value"], r["value_norm"], r["label"]),
                    )
            await db.execute("DELETE FROM contact_emails WHERE contact_id = ?", (loser,))

            # Circles: same pattern (UNIQUE on PK handles dupes).
            async with db.execute(
                "SELECT circle_id FROM contact_circles WHERE contact_id = ?",
                (loser,),
            ) as cur:
                for r in await cur.fetchall():
                    await db.execute(
                        "INSERT OR IGNORE INTO contact_circles (contact_id, circle_id) VALUES (?, ?)",
                        (winner_id, r["circle_id"]),
                    )
            # Interactions: just retarget (no unique on id).
            await db.execute(
                "UPDATE interactions SET contact_id = ? WHERE contact_id = ?",
                (winner_id, loser),
            )

            # Loser is now empty of references; delete it (cascade is moot at this point).
            await db.execute("DELETE FROM contacts WHERE id = ?", (loser,))

        # Re-sync winner's denormalized primary phone.
        async with db.execute(
            "SELECT value FROM contact_phones WHERE contact_id = ? ORDER BY is_primary DESC, id ASC LIMIT 1",
            (winner_id,),
        ) as cur:
            row = await cur.fetchone()
        primary = row["value"] if row else None
        await db.execute("UPDATE contacts SET phone = ? WHERE id = ?", (primary, winner_id))

        await db.commit()

    return RedirectResponse(url="/duplicates", status_code=303)


@router.post("/duplicates/dismiss")
async def dismiss_duplicate(
    contact_ids: list[int] = Form(default=[]),
    user_id: int = Depends(get_current_user_id),
):
    """Persist 'these are not duplicates' for every pair within this cluster."""
    if len(contact_ids) < 2:
        raise HTTPException(status_code=400, detail="need at least 2 ids")
    async with connect() as db:
        if not await _user_owns(db, user_id, contact_ids):
            raise HTTPException(status_code=404)
        ids = sorted(set(contact_ids))
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO dismissed_duplicates
                        (user_id, contact_a_id, contact_b_id)
                    VALUES (?, ?, ?)
                    """,
                    (user_id, a, b),
                )
        await db.commit()
    return RedirectResponse(url="/duplicates", status_code=303)
