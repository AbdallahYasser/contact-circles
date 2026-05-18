"""Entrypoint — runs FastAPI (uvicorn) and the aiogram bot in the same event loop.

Layout: one process, two co-running coroutines. SQLite is the shared store.
APScheduler runs the hourly reminder sweep inside the same loop.
"""
import asyncio
import logging

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from src import bot as bot_module
from src import config, reminders
from src.db import init_db
from src.routes import circles as circles_routes
from src.routes import contacts as contacts_routes
from src.routes import dashboard as dashboard_routes
from src.routes import duplicates as duplicates_routes
from src.routes import import_ as import_routes
from src.routes import login as login_routes

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Contact Circles")
    app.include_router(login_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(contacts_routes.router)
    app.include_router(circles_routes.router)
    app.include_router(import_routes.router)
    app.include_router(duplicates_routes.router)

    @app.get("/healthz", response_class=HTMLResponse)
    async def healthz():
        return "ok"

    return app


app = create_app()


async def _run() -> None:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set")
    if not config.SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET is not set")

    await init_db()

    bot, dp = await bot_module.build_bot_and_dispatcher()
    reminders.schedule(bot)

    # Run uvicorn + aiogram polling concurrently.
    server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=8080, log_level=config.LOG_LEVEL.lower())
    )

    async def _bot_poll():
        try:
            await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
        finally:
            await bot.session.close()

    logger.info("Starting web (8080) + bot polling")
    await asyncio.gather(server.serve(), _bot_poll())


if __name__ == "__main__":
    asyncio.run(_run())
