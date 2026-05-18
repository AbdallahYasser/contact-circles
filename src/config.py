"""Environment-backed config. Read once at startup."""
import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "/data/app.db")
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8080").rstrip("/")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEFAULT_TZ = os.getenv("TZ", "UTC")

_raw_allowed = os.getenv("ALLOWED_TELEGRAM_IDS", "").strip()
ALLOWED_TELEGRAM_IDS: set[int] = (
    {int(x) for x in _raw_allowed.split(",") if x.strip().isdigit()}
    if _raw_allowed else set()
)

BOT_USERNAME = ""
