"""SQLite (aiosqlite) helpers + schema bootstrap.

Tables:
  users            — one row per logged-in Telegram user
  circles          — per-user named circles with default reminder cadence
  contacts         — people the user wants to keep in touch with
  contact_circles  — many-to-many: one contact can be in many circles
  interactions     — append-only log of 'talked' events; updates last_contacted_at
"""
import logging
from contextlib import asynccontextmanager

import aiosqlite

from src import config

logger = logging.getLogger(__name__)

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id       INTEGER NOT NULL UNIQUE,
        telegram_username TEXT,
        first_name        TEXT,
        photo_url         TEXT,
        digest_hour       INTEGER NOT NULL DEFAULT 9,
        timezone          TEXT    NOT NULL DEFAULT 'UTC',
        created_at        TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS circles (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id              INTEGER NOT NULL,
        name                 TEXT    NOT NULL,
        color                TEXT    NOT NULL DEFAULT '#6366f1',
        default_cadence_days INTEGER NOT NULL DEFAULT 30,
        created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE(user_id, name),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contacts (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id           INTEGER NOT NULL,
        full_name         TEXT    NOT NULL,
        nickname          TEXT,
        phone             TEXT,
        telegram_handle   TEXT,
        birthday          TEXT,
        notes             TEXT,
        last_contacted_at TEXT,
        created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contact_circles (
        contact_id INTEGER NOT NULL,
        circle_id  INTEGER NOT NULL,
        PRIMARY KEY (contact_id, circle_id),
        FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE,
        FOREIGN KEY (circle_id)  REFERENCES circles(id)  ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS interactions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        contact_id  INTEGER NOT NULL,
        kind        TEXT    NOT NULL DEFAULT 'talked',
        note        TEXT,
        occurred_at TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_contacts_user      ON contacts(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_circles_user       ON circles(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_contact_circles_c  ON contact_circles(circle_id)",
    "CREATE INDEX IF NOT EXISTS idx_interactions_c     ON interactions(contact_id, occurred_at DESC)",
]


async def init_db() -> None:
    async with aiosqlite.connect(config.DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        for stmt in _SCHEMA:
            await db.execute(stmt)
        await db.commit()
    logger.info("Database initialised at %s", config.DB_PATH)


@asynccontextmanager
async def connect():
    """Yields a connection with row_factory set to sqlite3.Row + FK pragma on."""
    async with aiosqlite.connect(config.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
