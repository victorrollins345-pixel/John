"""All settings come from environment variables (set them in Railway → Variables)."""
from __future__ import annotations

import os

try:  # Load a local .env file when running on your own computer.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _id_set(name: str) -> set[int]:
    raw = os.environ.get(name, "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


# --- Telegram -----------------------------------------------------------------
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BOT_USERNAME = os.environ.get("BOT_USERNAME", "MiniSEOToolsBot")
# Optional: comma-separated Telegram user IDs allowed to use the bot. Empty = public bot.
ALLOWED_USER_IDS = _id_set("ALLOWED_USER_IDS")
# Optional: admins see /stats.
ADMIN_USER_IDS = _id_set("ADMIN_USER_IDS")
# Seconds a user must wait between commands (basic anti-abuse). 0 = off.
COOLDOWN_SECONDS = _int("COOLDOWN_SECONDS", 3)

# --- Storage (mount a Railway Volume at /data so tracking survives redeploys) --
DATA_DIR = os.environ.get("DATA_DIR", "/data" if os.path.isdir("/data") else "./data")
DB_PATH = os.path.join(DATA_DIR, "seo_bot.db")

# --- Free data sources ----------------------------------------------------------
PAGESPEED_API_KEY = os.environ.get("PAGESPEED_API_KEY", "").strip()  # optional, raises quota
SUGGEST_HL = os.environ.get("SUGGEST_HL", "en")  # Google Autocomplete language
SUGGEST_GL = os.environ.get("SUGGEST_GL", "us")  # Google Autocomplete country
BROKEN_LINKS_LIMIT = _int("BROKEN_LINKS_LIMIT", 100)

# --- DataForSEO (keyword volumes, backlinks, domain analytics, SERPs) ---------
DFS_LOGIN = os.environ.get("DATAFORSEO_LOGIN", "").strip()
DFS_PASSWORD = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
# 2840 = United States, 2566 = Nigeria, 2826 = United Kingdom, 2356 = India ...
DFS_LOCATION_CODE = _int("DATAFORSEO_LOCATION_CODE", 2840)
DFS_LANGUAGE_CODE = os.environ.get("DATAFORSEO_LANGUAGE_CODE", "en")

# --- Serper.dev (cheap alternative for SERP / rank checks only) ----------------
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "").strip()
SERPER_GL = os.environ.get("SERPER_GL", "us")
SERPER_HL = os.environ.get("SERPER_HL", "en")

# --- Rank tracking ------------------------------------------------------------------
MAX_TRACKED_PER_CHAT = _int("MAX_TRACKED_PER_CHAT", 20)
TRACK_HOUR_UTC = _int("TRACK_HOUR_UTC", 6)  # daily check time (UTC)


def dataforseo_enabled() -> bool:
    return bool(DFS_LOGIN and DFS_PASSWORD)


def serp_enabled() -> bool:
    return dataforseo_enabled() or bool(SERPER_API_KEY)
