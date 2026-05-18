"""Tests for Telegram Login Widget hash verification."""
import hashlib
import hmac
import os
import time


def _sign(data: dict, token: str) -> str:
    check = "\n".join(sorted(f"{k}={v}" for k, v in data.items()))
    secret = hashlib.sha256(token.encode()).digest()
    return hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()


async def test_verify_telegram_hash_accepts_valid_signature(monkeypatch):
    from src import auth, config
    monkeypatch.setattr(config, "BOT_TOKEN", "test:token-XYZ")

    payload = {
        "id": "12345",
        "first_name": "Test",
        "auth_date": str(int(time.time())),
    }
    payload["hash"] = _sign(payload, "test:token-XYZ")

    assert auth.verify_telegram_hash(payload) is True


async def test_verify_telegram_hash_rejects_tampered(monkeypatch):
    from src import auth, config
    monkeypatch.setattr(config, "BOT_TOKEN", "test:token-XYZ")

    payload = {
        "id": "12345",
        "first_name": "Test",
        "auth_date": str(int(time.time())),
    }
    payload["hash"] = _sign(payload, "test:token-XYZ")
    payload["first_name"] = "Mallory"  # tamper after signing

    assert auth.verify_telegram_hash(payload) is False


async def test_verify_telegram_hash_rejects_stale(monkeypatch):
    from src import auth, config
    monkeypatch.setattr(config, "BOT_TOKEN", "test:token-XYZ")

    payload = {
        "id": "12345",
        "first_name": "Test",
        "auth_date": str(int(time.time()) - 90000),  # 25h old
    }
    payload["hash"] = _sign(payload, "test:token-XYZ")

    assert auth.verify_telegram_hash(payload) is False


async def test_session_token_roundtrip(monkeypatch):
    from src import auth, config
    monkeypatch.setattr(config, "SESSION_SECRET", "unit-test-secret")
    token = auth.create_session_token(42)
    assert auth.decode_session_token(token) == 42


async def test_upsert_user_from_telegram_creates_then_updates():
    from src.auth import upsert_user_from_telegram
    from src.db import connect

    uid = await upsert_user_from_telegram({
        "id": "9001", "first_name": "Alice", "username": "alice"
    })
    assert isinstance(uid, int)

    uid2 = await upsert_user_from_telegram({
        "id": "9001", "first_name": "Alice B.", "username": "aliceb"
    })
    assert uid2 == uid  # same internal id

    async with connect() as db:
        async with db.execute(
            "SELECT first_name, telegram_username FROM users WHERE id = ?", (uid,)
        ) as cur:
            row = await cur.fetchone()
    assert row["first_name"] == "Alice B."
    assert row["telegram_username"] == "aliceb"
