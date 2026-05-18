"""Telegram Login Widget verification + JWT session cookies.

Spec: https://core.telegram.org/widgets/login#checking-authorization
Hash check pattern adapted from finance-web/src/auth.py.
"""
import hashlib
import hmac
import time

from fastapi import Cookie, HTTPException

from src import config
from src.db import connect

ALGORITHM = "HS256"
SESSION_DAYS = 30
SESSION_COOKIE = "session"


def verify_telegram_hash(data: dict) -> bool:
    """Verify the hash sent by the Telegram Login Widget.

    Returns True only when the HMAC matches AND the auth_date is < 24h old.
    """
    received_hash = data.get("hash", "")
    if not received_hash:
        return False

    check_data = {k: v for k, v in data.items() if k != "hash"}
    data_check_string = "\n".join(
        sorted(f"{k}={v}" for k, v in check_data.items())
    )
    secret_key = hashlib.sha256(config.BOT_TOKEN.encode()).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        return False

    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False
    if time.time() - auth_date > 86400:
        return False
    return True


def create_session_token(user_id: int) -> str:
    from jose import jwt
    exp = int(time.time()) + SESSION_DAYS * 86400
    return jwt.encode(
        {"user_id": user_id, "exp": exp},
        config.SESSION_SECRET,
        algorithm=ALGORITHM,
    )


def decode_session_token(token: str) -> int:
    from jose import JWTError, jwt
    try:
        payload = jwt.decode(token, config.SESSION_SECRET, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid session")
        return int(user_id)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid session")


def get_current_user_id(session: str | None = Cookie(default=None)) -> int:
    """FastAPI dependency — returns internal users.id from cookie or 401s."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return decode_session_token(session)


async def upsert_user_from_telegram(data: dict) -> int:
    """Upsert by telegram_id; return internal users.id."""
    tg_id = int(data["id"])
    async with connect() as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, telegram_username, first_name, photo_url)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                telegram_username = excluded.telegram_username,
                first_name        = excluded.first_name,
                photo_url         = excluded.photo_url
            """,
            (
                tg_id,
                data.get("username"),
                data.get("first_name"),
                data.get("photo_url"),
            ),
        )
        await db.commit()
        async with db.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (tg_id,)
        ) as cur:
            row = await cur.fetchone()
            return int(row["id"])


def is_telegram_user_allowed(tg_id: int) -> bool:
    """Optional gate via ALLOWED_TELEGRAM_IDS env var. Empty = allow anyone."""
    if not config.ALLOWED_TELEGRAM_IDS:
        return True
    return tg_id in config.ALLOWED_TELEGRAM_IDS
