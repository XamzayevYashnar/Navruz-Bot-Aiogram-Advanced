"""Telegram handler'lari, tugmalar va xabar yuborish yordamchilari."""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import reports
from pos import PosClient
from storage import State

log = logging.getLogger("navruz-bot.bot")

# DIQQAT: handler parametri `store` deb nomlangan, `state` EMAS.
# aiogram `state` nomini o'zining FSMContext'i uchun band qilgan va uni
# bizning obyektimiz ustidan yozib yuboradi.
dp = Dispatcher()

SEND_RETRIES = 3


# ---------------------------------------------------------------- tugmalar --


def sale_keyboard() -> InlineKeyboardMarkup:
    """Sotuv xabari ostidagi tugmalar. `info` — TZ'dagi asl tugma."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📋 To'liq ma'lumot", callback_data="info"),
        InlineKeyboardButton(text="📊 Bugungi hisobot", callback_data="today"),
    ]])


def info_keyboard() -> InlineKeyboardMarkup:
    """Ma'lumot xabari ostidagi tugma — xabarni o'rnida yangilaydi."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Yangilash", callback_data="refresh"),
        InlineKeyboardButton(text="📈 Statistika", callback_data="stats"),
    ]])


# ------------------------------------------------------------ yuborish -----


async def safe_send(bot: Bot, chat_id, text: str, **kwargs) -> None:
    """Telegramga xabar yuboradi, tarmoq uzilsa qayta urinadi.

    Mintaqadagi ulanish tez-tez uzilgani uchun kerak.
    """
    for attempt in range(1, SEND_RETRIES + 1):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return
        except Exception as e:
            if attempt == SEND_RETRIES:
                log.error("Xabar yuborilmadi (chat %s): %s", chat_id, e)
                return
            await asyncio.sleep(2 ** (attempt - 1))


async def broadcast(bot: Bot, text: str, **kwargs) -> None:
    """Barcha adminlarga yuboradi (ADMIN_CHAT_ID vergul bilan bir nechta bo'lishi mumkin)."""
    for chat_id in config.ADMIN_CHAT_IDS:
        await safe_send(bot, chat_id, text, **kwargs)


async def send_chart(bot: Bot, chat_id, store: State, caption: str = "") -> bool:
    """7 kunlik grafikni rasm qilib yuboradi. Ma'lumot yo'q bo'lsa False."""
    png = await asyncio.to_thread(reports.build_chart, store)   # matplotlib bloklaydi
    if not png:
        return False
    try:
        await bot.send_photo(
            chat_id, BufferedInputFile(png, filename="sotuv.png"), caption=caption or None
        )
        return True
    except Exception as e:
        log.error("Grafik yuborilmadi: %s", e)
        return False


async def setup_commands(bot: Bot) -> None:
    """Telegramda `/` bosilganda chiqadigan menyu."""
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Bot haqida"),
            BotCommand(command="check", description="Hozirgi qoldiq va narxlar"),
            BotCommand(command="today", description="Bugungi hisobot"),
            BotCommand(command="stats", description="7 kunlik statistika"),
        ])
    except Exception as e:
        log.warning("Buyruqlar menyusi o'rnatilmadi: %s", e)


# --------------------------------------------------------------- handlers --


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🍫 Salom! Men Dark Chocolate sotuvini kuzatib boraman.\n"
        "Mahsulot sotilishi bilan sizga xabar yuboraman.\n\n"
        "/check — hozirgi ma'lumotni ko'rish\n"
        "/today — bugungi hisobot\n"
        "/stats — 7 kunlik statistika"
    )


@dp.message(Command("check"))
async def cmd_check(message: Message, pos: PosClient, store: State) -> None:
    item = await pos.fetch_item(config.PRODUCT_KEYS[0])
    if item is None:
        await message.answer("⚠️ Ma'lumot olinmadi. Keyinroq urinib ko'ring.")
        return
    await message.answer(reports.format_info(item, store), reply_markup=info_keyboard())


@dp.message(Command("today"))
async def cmd_today(message: Message, pos: PosClient, store: State) -> None:
    items = await pos.fetch_all()
    await message.answer(reports.format_daily_report(store, items))


@dp.message(Command("stats"))
async def cmd_stats(message: Message, bot: Bot, store: State) -> None:
    await message.answer(reports.format_stats(store))
    await send_chart(bot, message.chat.id, store)


@dp.callback_query(F.data == "info")
async def cb_info(callback: CallbackQuery, pos: PosClient, store: State) -> None:
    """TZ'dagi asl tugma — yangi xabar bilan javob beradi (sotuv xabari saqlanib qoladi)."""
    item = await pos.fetch_item(config.PRODUCT_KEYS[0])
    if item is None:
        await callback.answer("⚠️ Ma'lumot olinmadi", show_alert=True)
        return
    await callback.message.answer(reports.format_info(item, store), reply_markup=info_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "refresh")
async def cb_refresh(callback: CallbackQuery, pos: PosClient, store: State) -> None:
    """Ma'lumotni o'sha xabarning o'zida yangilaydi — chat toza qoladi."""
    item = await pos.fetch_item(config.PRODUCT_KEYS[0])
    if item is None:
        await callback.answer("⚠️ Ma'lumot olinmadi", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            reports.format_info(item, store), reply_markup=info_keyboard()
        )
        await callback.answer("Yangilandi ✅")
    except Exception as e:
        # Ma'lumot o'zgarmagan bo'lsa Telegram "not modified" xatosi beradi — bu xato emas.
        if "not modified" in str(e).lower():
            await callback.answer("O'zgarish yo'q")
        else:
            log.warning("Xabarni yangilab bo'lmadi: %s", e)
            await callback.answer("⚠️ Yangilanmadi", show_alert=True)


@dp.callback_query(F.data == "today")
async def cb_today(callback: CallbackQuery, pos: PosClient, store: State) -> None:
    items = await pos.fetch_all()
    await callback.message.answer(reports.format_daily_report(store, items))
    await callback.answer()


@dp.callback_query(F.data == "stats")
async def cb_stats(callback: CallbackQuery, bot: Bot, store: State) -> None:
    await callback.message.answer(reports.format_stats(store))
    await send_chart(bot, callback.message.chat.id, store)
    await callback.answer()
