"""Bot holatini saqlash.

Ikki qatlam:
  1. Lokal `state.json` — tez va oddiy.
  2. Telegram'dagi PIN qilingan xabar — Render bepul tarifida disk saqlanmagani uchun
     zaxira nusxa. Bot qayta ishga tushganda o'sha xabardan qoldiqni o'qib oladi,
     shuning uchun restart paytidagi sotuv yo'qolmaydi.

Hech qanday baza kerak emas.
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta

import config

log = logging.getLogger("navruz-bot.storage")

STATE_MARKER = "STATE:"
_STATE_RE = re.compile(re.escape(STATE_MARKER) + r"(\{.*\})", re.S)


def today_str() -> str:
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


class State:
    """Qoldiq, kunlik statistika va yuborilgan ogohlantirishlar."""

    def __init__(self) -> None:
        self.stock: dict[str, float] = {}          # {product_key: oxirgi qoldiq}
        self.days: dict[str, dict] = {}            # {"2026-08-15": {"qty","rev","profit"}}
        self.last_report_date: str = ""            # kunlik hisobot yuborilgan sana
        self.token_warn_date: str = ""             # token ogohlantirishi yuborilgan sana
        self.low_stock_notified: dict[str, bool] = {}
        self.pinned_message_id: int | None = None

    # ------------------------------------------------------- serialization --

    def to_dict(self) -> dict:
        return {
            "v": 1,
            "stock": self.stock,
            "days": self.days,
            "last_report_date": self.last_report_date,
            "token_warn_date": self.token_warn_date,
            "low_stock_notified": self.low_stock_notified,
            "pinned_message_id": self.pinned_message_id,
        }

    def load_dict(self, data: dict) -> None:
        self.stock = {str(k): float(v) for k, v in (data.get("stock") or {}).items()}
        self.days = data.get("days") or {}
        self.last_report_date = data.get("last_report_date") or ""
        self.token_warn_date = data.get("token_warn_date") or ""
        self.low_stock_notified = data.get("low_stock_notified") or {}
        self.pinned_message_id = data.get("pinned_message_id")

    # ------------------------------------------------------------ statistika --

    def record_sale(self, qty: int, revenue: float, profit: float) -> None:
        """Sotuvni bugungi kun statistikasiga qo'shadi."""
        day = self.days.setdefault(today_str(), {"qty": 0, "rev": 0.0, "profit": 0.0})
        day["qty"] += qty
        day["rev"] += revenue
        day["profit"] += profit
        self._trim_history()

    def _trim_history(self) -> None:
        """Faqat oxirgi HISTORY_DAYS kun saqlansin (pin xabari cheklovi 4096 belgi)."""
        if len(self.days) <= config.HISTORY_DAYS:
            return
        for day in sorted(self.days)[: -config.HISTORY_DAYS]:
            del self.days[day]

    def day_stats(self, day: str) -> dict:
        return self.days.get(day) or {"qty": 0, "rev": 0.0, "profit": 0.0}

    def today_stats(self) -> dict:
        return self.day_stats(today_str())

    def last_days(self, n: int) -> list[tuple[str, dict]]:
        """Oxirgi n kun (bugundan orqaga), sotuv bo'lmagan kunlar ham nol bilan."""
        base = datetime.now(config.TZ).date()
        out = []
        for i in range(n - 1, -1, -1):
            day = (base - timedelta(days=i)).strftime("%Y-%m-%d")
            out.append((day, self.day_stats(day)))
        return out

    def avg_daily_sales(self, n: int = 7) -> float:
        """Oxirgi n kunning o'rtacha sotuvi — faqat sotuv bo'lgan kunlar hisobga olinadi.

        Bot yangi o'rnatilganda ma'lumot kam bo'ladi, shuning uchun bo'sh kunlarni
        qo'shib o'rtachani sun'iy pasaytirmaymiz.
        """
        active = [d["qty"] for _, d in self.last_days(n) if d["qty"] > 0]
        return sum(active) / len(active) if active else 0.0

    def days_left(self, stock: float, n: int = 7) -> float | None:
        """Qoldiq necha kunga yetadi (o'rtacha sotuvga qarab)."""
        avg = self.avg_daily_sales(n)
        return stock / avg if avg > 0 else None

    # ---------------------------------------------------------- lokal fayl --

    def save_file(self) -> None:
        try:
            with open(config.STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False)
        except OSError as e:
            log.warning("state.json yozilmadi: %s", e)

    def load_file(self) -> bool:
        if not os.path.exists(config.STATE_FILE):
            return False
        try:
            with open(config.STATE_FILE, encoding="utf-8") as f:
                self.load_dict(json.load(f))
            log.info("Holat state.json dan o'qildi.")
            return True
        except (OSError, json.JSONDecodeError) as e:
            log.warning("state.json o'qilmadi: %s", e)
            return False


# ------------------------------------------------ Telegram pin xabari (zaxira) --


def _render_pin_text(state: State) -> str:
    """Pin xabari matni: odamga tushunarli qism + mashina o'qiydigan JSON."""
    stock_lines = "\n".join(f"📦 Qoldiq: {int(v)} dona" for v in state.stock.values())
    today = state.today_stats()
    return (
        "📌 Bot xotirasi — bu xabarni o'chirmang!\n"
        f"{stock_lines}\n"
        f"📊 Bugun sotildi: {int(today['qty'])} dona\n"
        f"🕒 Yangilandi: {datetime.now(config.TZ).strftime('%Y-%m-%d %H:%M')}\n\n"
        f"{STATE_MARKER}{json.dumps(state.to_dict(), ensure_ascii=False, separators=(',', ':'))}"
    )


async def load_from_pin(bot, state: State) -> bool:
    """Pin qilingan xabardan holatni tiklaydi (lokal fayl yo'q bo'lsa)."""
    if not config.ADMIN_CHAT_IDS:
        return False
    try:
        chat = await bot.get_chat(config.ADMIN_CHAT_IDS[0])
        pinned = getattr(chat, "pinned_message", None)
        if not pinned or not pinned.text:
            return False
        match = _STATE_RE.search(pinned.text)
        if not match:
            return False
        state.load_dict(json.loads(match.group(1)))
        state.pinned_message_id = pinned.message_id
        log.info("Holat Telegram pin xabaridan tiklandi.")
        return True
    except Exception as e:
        log.warning("Pin xabaridan o'qib bo'lmadi: %s", e)
        return False


async def save(bot, state: State) -> None:
    """Holatni faylga va pin xabariga yozadi.

    Birinchi marta — xabar yuborilib pin qilinadi. Keyin faqat TAHRIRLANADI,
    shuning uchun har safar bildirishnoma chiqmaydi.
    """
    state.save_file()

    if not config.ADMIN_CHAT_IDS:
        return
    chat_id = config.ADMIN_CHAT_IDS[0]
    text = _render_pin_text(state)

    try:
        if state.pinned_message_id:
            await bot.edit_message_text(text=text, chat_id=chat_id,
                                        message_id=state.pinned_message_id)
            return
    except Exception as e:
        # "message is not modified" yoki xabar o'chirilgan bo'lishi mumkin.
        if "not modified" in str(e).lower():
            return
        log.warning("Pin xabarini tahrirlab bo'lmadi (%s), yangisi yaratiladi.", e)
        state.pinned_message_id = None

    try:
        msg = await bot.send_message(chat_id, text, disable_notification=True)
        state.pinned_message_id = msg.message_id
        await bot.pin_chat_message(chat_id, msg.message_id, disable_notification=True)
        state.save_file()
        log.info("Yangi holat xabari yaratilib pin qilindi.")
    except Exception as e:
        # Pin qilish taqiqlangan bo'lsa ham bot ishlashda davom etadi (lokal fayl bor).
        log.warning("Holatni Telegramga saqlab bo'lmadi: %s", e)
