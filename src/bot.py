"""Telegram bot: /start command + callback buttons from the digest message."""
import datetime
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src import config
from src.db import connect

logger = logging.getLogger(__name__)
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        f"Hi {message.from_user.first_name}! 👋\n\n"
        "I'm your contact-circles reminder bot. Open the web app to add people "
        "and organise them into circles. I'll DM you a daily digest of who to "
        "reach out to.\n\n"
        f"➡️  {config.APP_BASE_URL}/login"
    )
    await message.answer(text)


async def _user_id_for_tg(tg_id: int) -> int | None:
    async with connect() as db:
        async with db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row["id"]) if row else None


async def _owns_contact(db, user_id: int, contact_id: int) -> bool:
    async with db.execute(
        "SELECT 1 FROM contacts WHERE id = ? AND user_id = ?",
        (contact_id, user_id),
    ) as cur:
        return (await cur.fetchone()) is not None


@router.callback_query(F.data.startswith("t:"))
async def cb_talked(cb: CallbackQuery) -> None:
    user_id = await _user_id_for_tg(cb.from_user.id)
    if user_id is None:
        await cb.answer("Not logged in")
        return
    try:
        contact_id = int(cb.data.split(":", 1)[1])
    except (ValueError, IndexError):
        await cb.answer()
        return
    async with connect() as db:
        if not await _owns_contact(db, user_id, contact_id):
            await cb.answer("Unknown contact")
            return
        await db.execute(
            "INSERT INTO interactions (contact_id, kind) VALUES (?, 'talked')",
            (contact_id,),
        )
        await db.execute(
            "UPDATE contacts SET last_contacted_at = datetime('now') WHERE id = ?",
            (contact_id,),
        )
        await db.commit()
    await cb.answer("✅ Logged")


@router.callback_query(F.data.startswith("s:"))
async def cb_snooze(cb: CallbackQuery) -> None:
    user_id = await _user_id_for_tg(cb.from_user.id)
    if user_id is None:
        await cb.answer("Not logged in")
        return
    parts = cb.data.split(":")
    if len(parts) != 3:
        await cb.answer()
        return
    try:
        contact_id = int(parts[1])
        days = int(parts[2])
    except ValueError:
        await cb.answer()
        return
    # Snooze = bump last_contacted_at forward, so cadence math says "not overdue"
    async with connect() as db:
        if not await _owns_contact(db, user_id, contact_id):
            await cb.answer("Unknown contact")
            return
        await db.execute(
            "UPDATE contacts SET last_contacted_at = datetime('now', ? || ' days') WHERE id = ?",
            (f"+{days}", contact_id),
        )
        await db.commit()
    await cb.answer(f"💤 Snoozed {days}d")


@router.callback_query(F.data.startswith("k:"))
async def cb_skip(cb: CallbackQuery) -> None:
    # Skip is a no-op acknowledgement; the digest tomorrow will surface them again.
    await cb.answer("⏭ Skipped for today")


async def build_bot_and_dispatcher() -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    me = await bot.get_me()
    config.BOT_USERNAME = me.username or ""
    logger.info("Bot @%s ready", config.BOT_USERNAME)

    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp
