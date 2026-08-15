"""Fon vazifalari: sotuv monitoringi, kunlik hisobot, token nazorati, self-ping."""

import asyncio
import logging
from datetime import datetime

import aiohttp

import config
import reports
import storage
from bot import broadcast, safe_send, sale_keyboard, send_chart
from pos import PosClient
from storage import State, today_str

log = logging.getLogger("navruz-bot.monitor")


# ------------------------------------------------------------ monitoring ---


async def monitor_stock(bot, pos: PosClient, state: State) -> None:
    """Har POLL_INTERVAL soniyada qoldiqni tekshiradi va kamayganda xabar yuboradi."""
    token_problem_notified = False

    while True:
        try:
            items = await pos.fetch_all()

            if not items:
                if pos.refresh_failed and not token_problem_notified:
                    token_problem_notified = True
                    await broadcast(bot, reports.format_login_failed())
                else:
                    log.warning("Ma'lumot olinmadi, keyingi urinishni kutamiz.")
            else:
                token_problem_notified = False
                pos.refresh_failed = False
                changed = False

                for key, item in items.items():
                    if await _check_item(bot, state, key, item):
                        changed = True

                if changed:
                    await storage.save(bot, state)

        except Exception as e:
            # Monitoring hech qachon to'xtamasligi kerak.
            log.exception("Monitoring siklida kutilmagan xato: %s", e)

        await asyncio.sleep(config.POLL_INTERVAL)


async def _check_item(bot, state: State, key: str, item: dict) -> bool:
    """Bitta mahsulotni tekshiradi. Holat o'zgargan bo'lsa True qaytaradi."""
    stock = float(item.get("stock") or 0)
    prev = state.stock.get(key)

    if prev is None:
        # Birinchi ishga tushish — bu qiymat "boshlang'ich", xabar yuborilmaydi.
        log.info("Boshlang'ich qoldiq (%s): %s dona", key, int(stock))
        state.stock[key] = stock
        return True

    if stock < prev:
        sold = int(prev - stock)
        price = float(item.get("price") or 0)
        revenue = sold * price
        profit = sold * reports.profit_per_unit(item)

        log.info("SOTUV: %s dona, qoldiq %s", sold, int(stock))
        state.record_sale(sold, revenue, profit)
        state.stock[key] = stock

        await broadcast(
            bot, reports.format_sale(item, sold, stock), reply_markup=sale_keyboard()
        )
        await _check_low_stock(bot, state, key, item, stock)
        return True

    if stock > prev:
        # Kirim — xabar shart emas, faqat qiymat yangilanadi.
        log.info("Kirim: qoldiq %s -> %s", int(prev), int(stock))
        state.stock[key] = stock
        # Qoldiq to'ldirilgan bo'lsa, ogohlantirishni qaytadan yoqamiz.
        if stock > config.LOW_STOCK_THRESHOLD:
            state.low_stock_notified[key] = False
        return True

    return False


async def _check_low_stock(bot, state: State, key: str, item: dict, stock: float) -> None:
    """Qoldiq chegaradan tushganda bir marta ogohlantiradi."""
    if stock > config.LOW_STOCK_THRESHOLD:
        state.low_stock_notified[key] = False
        return

    if state.low_stock_notified.get(key):
        return          # allaqachon aytilgan, takrorlamaymiz

    state.low_stock_notified[key] = True
    await broadcast(bot, reports.format_low_stock(item, stock, state))


# ------------------------------------------------------------ rejalar ------


async def scheduler(bot, pos: PosClient, state: State) -> None:
    """Har daqiqada tekshiradi: kunlik hisobot vaqti keldimi, token tugayaptimi."""
    try:
        report_hour, report_minute = (int(x) for x in config.REPORT_TIME.split(":"))
    except ValueError:
        log.warning("REPORT_TIME noto'g'ri (%s), 21:00 ishlatiladi.", config.REPORT_TIME)
        report_hour, report_minute = 21, 0

    while True:
        try:
            now = datetime.now(config.TZ)
            today = today_str()

            # 1) Kunlik hisobot
            due = (now.hour, now.minute) >= (report_hour, report_minute)
            if due and state.last_report_date != today:
                state.last_report_date = today
                items = await pos.fetch_all()
                text = reports.format_daily_report(state, items)
                for chat_id in config.ADMIN_CHAT_IDS:
                    await safe_send(bot, chat_id, text)
                    await send_chart(bot, chat_id, state)
                await storage.save(bot, state)
                log.info("Kunlik hisobot yuborildi.")

            # 2) Token muddati tugayaptimi
            await _check_token_expiry(bot, pos, state, now, today)

        except Exception as e:
            log.exception("Scheduler xatosi: %s", e)

        await asyncio.sleep(60)


async def _check_token_expiry(bot, pos: PosClient, state: State, now, today: str) -> None:
    """Token tugashiga TOKEN_WARN_DAYS kun qolganda ogohlantiradi (kuniga bir marta).

    Avtomatik login sozlangan bo'lsa bot tokenni o'zi tiklaydi — bezovta qilmaymiz.
    """
    if config.POS_PASSWORD:
        return

    expiry = pos.access_expiry()
    if expiry is None or state.token_warn_date == today:
        return

    days_left = (expiry - now).total_seconds() / 86400
    if days_left > config.TOKEN_WARN_DAYS:
        return

    state.token_warn_date = today
    await broadcast(bot, reports.format_token_warning(max(days_left, 0), expiry))
    await storage.save(bot, state)
    log.warning("Token ogohlantirishi yuborildi: %.1f kun qoldi", days_left)


# ------------------------------------------------------------ self-ping ----


async def self_ping(session: aiohttp.ClientSession) -> None:
    """Render bepul tarifi 15 daqiqa harakatsizlikda uxlaydi — uxlagan bot sotuvni
    ko'rmaydi. Shuning uchun bot o'z manziliga muntazam so'rov yuborib turadi.
    """
    if not config.SELF_URL:
        log.info("SELF_URL yo'q — self-ping o'chirilgan (lokal rejim).")
        return

    log.info("Self-ping yoqildi: %s (har %s soniyada)", config.SELF_URL, config.SELF_PING_INTERVAL)
    while True:
        await asyncio.sleep(config.SELF_PING_INTERVAL)
        try:
            async with session.get(config.SELF_URL, timeout=30) as resp:
                log.debug("Self-ping: HTTP %s", resp.status)
        except Exception as e:
            log.warning("Self-ping xatosi: %s", e)
