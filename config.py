"""Barcha sozlamalar — faqat env var'lardan o'qiladi."""

import logging
import os
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return int(default)


def _csv(name: str, default: str = "") -> list[str]:
    """Vergul bilan ajratilgan ro'yxat: "111,222" -> ["111", "222"]."""
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


# --- Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_CHAT_IDS = _csv("ADMIN_CHAT_ID")          # bir nechta bo'lishi mumkin

# --- POS API ---
BASE_URL = os.getenv("BASE_URL", "https://bj33zdcumr.yespos.uz/").rstrip("/")
BRANCH_ID = os.getenv("BRANCH_ID", "1")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN", "")
REFRESH_TOKEN = os.getenv("REFRESH_TOKEN", "")

# --- Avtomatik login (token tugaganda) ---
# YesPOS'da alohida "refresh" endpoint YO'Q. Ikki mexanizm ishlatiladi:
#   1) Server tokenni yangilaganda javob header'ida X-Refresh-Token yuboradi;
#   2) 401 bo'lsa — signIn orqali qaytadan login qilinadi.
AUTH_URL = os.getenv("AUTH_URL", "https://m1.yespos.uz").rstrip("/")
POS_ORG_ID = os.getenv("POS_ORG_ID", "")
POS_LOGIN = os.getenv("POS_LOGIN", "")
POS_PASSWORD = os.getenv("POS_PASSWORD", "")
# Hisob bloklanmasligi uchun login urinishlari orasidagi eng kichik oraliq (soniya).
LOGIN_MIN_INTERVAL = _int("LOGIN_MIN_INTERVAL", "60")

# --- Mahsulot(lar) ---
# Bitta mahsulot: PRODUCT_KEY=16793
# Kelajakda ko'paytirish: PRODUCT_KEY=16793,16794 — kod o'zgarmaydi.
PRODUCT_KEYS = _csv("PRODUCT_KEY", "16793")
PRODUCT_ID = os.getenv("PRODUCT_ID", "6794")
# Sotuv xabarining sarlavhasidagi qisqa nom (bitta mahsulot rejimida).
PRODUCT_LABEL = os.getenv("PRODUCT_LABEL", "Dark Chocolate")

# --- Monitoring ---
POLL_INTERVAL = _int("POLL_INTERVAL", "30")
LOW_STOCK_THRESHOLD = _int("LOW_STOCK_THRESHOLD", "20")

# --- Hisobot ---
REPORT_TIME = os.getenv("REPORT_TIME", "21:00")      # HH:MM, mahalliy vaqt
TIMEZONE = os.getenv("TIMEZONE", "Asia/Tashkent")
TOKEN_WARN_DAYS = _int("TOKEN_WARN_DAYS", "3")       # necha kun oldin ogohlantirsin

# --- Render / web server ---
PORT = _int("PORT", "10000")
# Render o'zi beradi; lokalda bo'sh qolsa self-ping o'chadi.
SELF_URL = (os.getenv("RENDER_EXTERNAL_URL") or os.getenv("SELF_URL", "")).rstrip("/")
SELF_PING_INTERVAL = _int("SELF_PING_INTERVAL", "600")   # 10 daqiqa

# --- Xotira ---
STATE_FILE = os.getenv("STATE_FILE", "state.json")
HISTORY_DAYS = 14   # necha kunlik statistika saqlansin

def _load_tz(name: str):
    """Vaqt zonasi. `tzdata` o'rnatilmagan tizimda bot yiqilmasin — UTC+5 ga tushamiz."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, KeyError, ValueError):
        logging.getLogger("navruz-bot").warning(
            "Vaqt zonasi '%s' topilmadi (tzdata yo'qmi?) — UTC+5 ishlatiladi.", name
        )
        return timezone(timedelta(hours=5))     # O'zbekiston vaqti


TZ = _load_tz(TIMEZONE)


def validate() -> None:
    """Ishga tushishdan oldin majburiy sozlamalarni tekshiradi."""
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN env var majburiy!")
    if not ADMIN_CHAT_IDS:
        raise SystemExit("ADMIN_CHAT_ID env var majburiy!")
