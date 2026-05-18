"""Reminder sweep: find overdue contacts per user, send daily Telegram digest.

A contact's effective cadence is the **minimum** default_cadence_days across
all the circles it belongs to. A contact with no circle gets a 90-day fallback.
A contact with no last_contacted_at is treated as last contacted at created_at.
"""
import datetime
import logging
from typing import Any

import pytz
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from src import config
from src.db import connect

logger = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

FALLBACK_CADENCE_DAYS = 90
DIGEST_LIMIT = 5


async def overdue_contacts_for_user(user_id: int, limit: int = DIGEST_LIMIT) -> list[dict]:
    """Return contacts ranked by how overdue they are (most overdue first).

    Effective cadence = MIN(circle.default_cadence_days) across the contact's
    circles, or FALLBACK_CADENCE_DAYS if the contact has no circle.
    """
    async with connect() as db:
        async with db.execute(
            """
            SELECT
                c.id,
                c.full_name,
                c.nickname,
                COALESCE(c.last_contacted_at, c.created_at) AS reference_at,
                COALESCE(MIN(cl.default_cadence_days), ?) AS cadence_days,
                GROUP_CONCAT(cl.name, ', ') AS circle_names
            FROM contacts c
            LEFT JOIN contact_circles cc ON cc.contact_id = c.id
            LEFT JOIN circles cl ON cl.id = cc.circle_id
            WHERE c.user_id = ?
            GROUP BY c.id
            """,
            (FALLBACK_CADENCE_DAYS, user_id),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]

    now = datetime.datetime.utcnow()
    enriched = []
    for r in rows:
        ref = _parse_dt(r["reference_at"])
        days = (now - ref).total_seconds() / 86400.0
        cadence = max(int(r["cadence_days"] or FALLBACK_CADENCE_DAYS), 1)
        overdue_ratio = days / cadence
        if overdue_ratio < 1.0:
            continue
        r["days_since"] = int(days)
        r["cadence_days"] = cadence
        r["overdue_ratio"] = overdue_ratio
        enriched.append(r)

    enriched.sort(key=lambda x: x["overdue_ratio"], reverse=True)
    return enriched[:limit]


def _parse_dt(s: str | None) -> datetime.datetime:
    if not s:
        return datetime.datetime.utcnow()
    # SQLite default is 'YYYY-MM-DD HH:MM:SS'
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return datetime.datetime.utcnow()


async def send_digest_to_user(bot, user_id: int) -> None:
    overdue = await overdue_contacts_for_user(user_id)
    if not overdue:
        return

    async with connect() as db:
        async with db.execute(
            "SELECT telegram_id, first_name FROM users WHERE id = ?", (user_id,)
        ) as cur:
            user = await cur.fetchone()
    if not user:
        return

    lines = [f"👋 People you haven't talked to in a while:\n"]
    for i, c in enumerate(overdue, 1):
        circles = f" ({c['circle_names']})" if c["circle_names"] else ""
        lines.append(
            f"<b>{i}. {c['full_name']}</b>{circles} — {c['days_since']}d "
            f"(cadence {c['cadence_days']}d)"
        )
    text = "\n".join(lines)

    # One inline-keyboard row per contact: ✅ Talked / 💤 Snooze 3d / ⏭ Skip
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"✅ {c['full_name']}", callback_data=f"t:{c['id']}"),
                InlineKeyboardButton(text="💤 3d",  callback_data=f"s:{c['id']}:3"),
                InlineKeyboardButton(text="⏭",      callback_data=f"k:{c['id']}"),
            ]
            for c in overdue
        ]
    )
    try:
        await bot.send_message(
            user["telegram_id"], text, parse_mode="HTML", reply_markup=keyboard
        )
    except Exception as e:
        logger.error("Failed to send digest to user %d: %s", user_id, e)


async def hourly_sweep(bot) -> None:
    """Fires every hour. Sends digest to users whose local digest_hour matches."""
    async with connect() as db:
        async with db.execute(
            "SELECT id, timezone, digest_hour FROM users"
        ) as cur:
            users = [dict(r) for r in await cur.fetchall()]

    for u in users:
        try:
            tz = pytz.timezone(u.get("timezone") or "UTC")
        except pytz.UnknownTimeZoneError:
            tz = pytz.UTC
        local_hour = datetime.datetime.now(tz).hour
        if local_hour == int(u["digest_hour"]):
            await send_digest_to_user(bot, u["id"])


def schedule(bot) -> None:
    scheduler.add_job(
        hourly_sweep,
        trigger=CronTrigger(minute=0),
        args=[bot],
        id="hourly_sweep",
        replace_existing=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    logger.info("Reminder scheduler started (hourly sweep)")
