"""Login page + Telegram Login Widget callback."""
import logging
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src import auth, config

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


def _login_domain() -> str:
    """The bare host BotFather was told to whitelist (no scheme, no port)."""
    parsed = urlparse(config.APP_BASE_URL)
    return parsed.hostname or "localhost"


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "bot_username": config.BOT_USERNAME,
            "login_domain": _login_domain(),
        },
    )


@router.get("/auth/telegram")
async def telegram_callback(request: Request):
    """Telegram Login Widget posts query params here after auth. We verify,
    upsert, and set the session cookie."""
    data = dict(request.query_params)
    if not data:
        raise HTTPException(status_code=400, detail="Missing auth params")

    if not auth.verify_telegram_hash(data):
        logger.warning("Telegram login hash check failed for %s", data.get("id"))
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")

    tg_id = int(data["id"])
    if not auth.is_telegram_user_allowed(tg_id):
        raise HTTPException(status_code=403, detail="Not on the allow list")

    user_id = await auth.upsert_user_from_telegram(data)
    token = auth.create_session_token(user_id)

    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        max_age=auth.SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=config.APP_BASE_URL.startswith("https://"),
    )
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(auth.SESSION_COOKIE)
    return resp
