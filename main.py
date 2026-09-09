"""
Dark Chocolate sotuv monitoringi — Telegram bot (aiogram 3).

Ishga tushirish: python main.py

Bitta asyncio event loop'da parallel ishlaydi:
  • aiohttp web server  — Render bepul tarifi ochiq portni talab qiladi
  • bot polling         — buyruq va tugmalar
  • monitoring          — qoldiq kamayganini kuzatish
  • scheduler           — kunlik hisobot, token nazorati
  • self-ping           — Render uxlab qolmasligi uchun
"""

import asyncio
import logging

import aiohttp
from aiohttp import web

import bot as bot_module
import config
import monitor
import storage
from pos import PosClient
from storage import State

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("navruz-bot")

from aiogram import Bot  # noqa: E402  (logging sozlangandan keyin)


# ------------------------------------------------------------ web server ----


async def handle_health(request: web.Request) -> web.Response:
    """Render health check + self-ping uchun."""
    return web.Response(text="OK")


async def handle_status(request: web.Request) -> web.Response:
    """Diagnostика: bot tirikmi, qoldiq qancha, token qachon tugaydi."""
    state: State = request.app["state"]
    pos: PosClient = request.app["pos"]
    expiry = pos.access_expiry()
    return web.json_response({
        "status": "ok",
        "stock": state.stock,
        "today": state.today_stats(),
        "token_expires": expiry.isoformat() if expiry else None,
        "poll_interval": config.POLL_INTERVAL,
    })


async def start_web_server(state: State, pos: PosClient) -> web.AppRunner:
    app = web.Application()
    app["state"] = state
    app["pos"] = pos
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/status", handle_status)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    log.info("Web server ishga tushdi: 0.0.0.0:%s", config.PORT)
    return runner


# ------------------------------------------------------------------ main ----


async def main() -> None:
    config.validate()

    # parse_mode qo'yilmagan — mahsulot nomida <, & bo'lsa ham xato bermaydi.
    telegram = Bot(token=config.BOT_TOKEN)

    async with aiohttp.ClientSession() as session:
        pos = PosClient(session)
        state = State()

        # Holatni tiklash: avval lokal fayl, bo'lmasa Telegram pin xabari.
        if not state.load_file():
            await storage.load_from_pin(telegram, state)

        if state.stock:
            log.info("Oldingi qoldiq tiklandi: %s", state.stock)

        expiry = pos.access_expiry()
        if expiry:
            log.info("Access token muddati: %s", expiry.strftime("%Y-%m-%d %H:%M"))

        # Handler'lar uchun umumiy obyektlar.
        # "store" nomi ataylab — "state" ni aiogram FSMContext uchun ishlatadi.
        bot_module.dp["pos"] = pos
        bot_module.dp["store"] = state

        runner = await start_web_server(state, pos)
        await bot_module.setup_commands(telegram)

        tasks = [
            asyncio.create_task(monitor.monitor_stock(telegram, pos, state), name="monitor"),
            asyncio.create_task(monitor.scheduler(telegram, pos, state), name="scheduler"),
            asyncio.create_task(monitor.self_ping(session), name="self-ping"),
        ]
        log.info("Monitoring boshlandi (har %s soniyada).", config.POLL_INTERVAL)

        try:
            await telegram.delete_webhook(drop_pending_updates=True)
            await bot_module.dp.start_polling(telegram)
        finally:
            for task in tasks:
                task.cancel()
            state.save_file()
            await runner.cleanup()
            await telegram.session.close()
            log.info("Bot to'xtatildi.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass