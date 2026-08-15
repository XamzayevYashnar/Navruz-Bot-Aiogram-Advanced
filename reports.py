"""Xabar matnlari va hisobotlar.

MUHIM: `format_info()` va `format_sale()` ning asosiy qatorlari TZ'dagi
formatga aynan mos — faqat ustiga yangi qatorlar qo'shilgan.
"""

import io
from datetime import datetime

import config
from storage import State, today_str

MONTHS_UZ = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]


def fmt_money(value) -> str:
    """35000 -> '35 000'."""
    try:
        return f"{float(value):,.0f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(value)


def fmt_date(day: str) -> str:
    """'2026-08-15' -> '15-avgust'."""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
        return f"{d.day}-{MONTHS_UZ[d.month - 1]}"
    except ValueError:
        return day


def item_label(item: dict) -> str:
    """Sotuv sarlavhasidagi qisqa nom."""
    if len(config.PRODUCT_KEYS) == 1:
        return config.PRODUCT_LABEL
    return str(item.get("name", "Mahsulot")).strip()


def days_left_text(stock: float, avg: float) -> str:
    """'⏳ Taxminan 5 kunga yetadi' / bir kundan kam qolsa boshqacha yoziladi."""
    if avg <= 0:
        return ""
    days = stock / avg
    if days < 1:
        return "⏳ Bugun tugashi mumkin!"
    if days < 2:
        return "⏳ Taxminan 1 kunga yetadi"
    return f"⏳ Taxminan {days:.0f} kunga yetadi"


def profit_per_unit(item: dict) -> float:
    try:
        return float(item.get("price") or 0) - float(item.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


# ------------------------------------------------------------ asosiy matnlar --


def format_info(item: dict, state: State | None = None) -> str:
    """Mahsulotning to'liq ma'lumoti (TZ formati + bashorat)."""
    text = (
        f"📦 {str(item.get('name', '-')).strip()}\n"
        f"SKU: {item.get('sku', '-')}\n"
        f"💰 Narx: {fmt_money(item.get('price', 0))} so'm\n"
        f"🏷 Tannarx: {fmt_money(item.get('cost', 0))} so'm\n"
        f"📊 Margin: {item.get('margin', 0)}%\n"
        f"📦 Qoldiq: {item.get('stock', 0)} dona"
    )

    if state is None:
        return text

    stock = float(item.get("stock") or 0)
    avg = state.avg_daily_sales()
    if avg > 0:
        text += f"\n\n📉 Kuniga o'rtacha: {avg:.1f} dona\n{days_left_text(stock, avg)}"

    today = state.today_stats()
    if today["qty"] > 0:
        text += f"\n📊 Bugun sotildi: {int(today['qty'])} dona"

    return text


def format_sale(item: dict, sold: int, stock: float) -> str:
    """Sotuv xabari — TZ formati + tushum va foyda."""
    price = float(item.get("price") or 0)
    revenue = sold * price
    profit = sold * profit_per_unit(item)

    return (
        f"🍫 {item_label(item)} sotildi!\n"
        f"Sotilgan: {sold} dona\n"
        f"💵 Tushum: {fmt_money(revenue)} so'm\n"
        f"📈 Foyda: {fmt_money(profit)} so'm\n"
        f"Qolgan qoldiq: {int(stock)} dona"
    )


def format_low_stock(item: dict, stock: float, state: State) -> str:
    """Qoldiq tugayotgani haqida ogohlantirish."""
    text = (
        f"⚠️ Qoldiq tugayapti!\n"
        f"📦 {item_label(item)}: {int(stock)} dona qoldi"
    )
    avg = state.avg_daily_sales()
    if avg > 0:
        text += f"\n📉 Kuniga o'rtacha {avg:.1f} dona ketyapti\n{days_left_text(stock, avg)}"
    return text + "\n🛒 Buyurtma berish vaqti keldi."


def format_daily_report(state: State, items: dict[str, dict], day: str | None = None) -> str:
    """Kunlik hisobot + kechagi kun bilan solishtirish."""
    day = day or today_str()
    stats = state.day_stats(day)

    lines = [f"📊 Bugungi hisobot ({fmt_date(day)})", ""]

    if stats["qty"] == 0:
        lines.append("Bugun sotuv bo'lmadi.")
    else:
        lines += [
            f"🍫 Sotildi: {int(stats['qty'])} dona",
            f"💵 Tushum: {fmt_money(stats['rev'])} so'm",
            f"📈 Foyda: {fmt_money(stats['profit'])} so'm",
        ]

    for item in items.values():
        lines.append(f"📦 Qoldiq: {int(float(item.get('stock') or 0))} dona")

    # Kechagi kun bilan solishtirish
    days = state.last_days(2)
    if len(days) == 2:
        yesterday = days[0][1]["qty"]
        today_qty = stats["qty"]
        if yesterday > 0:
            diff = (today_qty - yesterday) / yesterday * 100
            arrow = "📈" if diff > 0 else ("📉" if diff < 0 else "➖")
            lines.append(f"\nKechagiga nisbatan: {diff:+.0f}% {arrow}")
        elif today_qty > 0:
            lines.append("\nKecha sotuv bo'lmagan edi 📈")

    avg = state.avg_daily_sales()
    if avg > 0 and items:
        stock = float(next(iter(items.values())).get("stock") or 0)
        lines.append(days_left_text(stock, avg))

    return "\n".join(lines)


def format_stats(state: State) -> str:
    """7 kunlik statistika jadvali."""
    lines = ["📈 Oxirgi 7 kun", ""]
    total_qty = total_rev = total_profit = 0.0

    for day, stats in state.last_days(7):
        qty = int(stats["qty"])
        total_qty += qty
        total_rev += stats["rev"]
        total_profit += stats["profit"]
        bar = "█" * min(int(qty), 20) if qty else "·"
        lines.append(f"{fmt_date(day):>12} │ {bar} {qty}")

    lines += [
        "",
        f"Jami: {int(total_qty)} dona",
        f"💵 Tushum: {fmt_money(total_rev)} so'm",
        f"📈 Foyda: {fmt_money(total_profit)} so'm",
    ]
    avg = state.avg_daily_sales()
    if avg > 0:
        lines.append(f"📊 Kuniga o'rtacha: {avg:.1f} dona")
    return "\n".join(lines)


def format_login_failed() -> str:
    """Token tugagan va avtomatik login ham ishlamagan holat."""
    if config.POS_PASSWORD:
        return (
            "🔑 POS'ga kira olmadim!\n\n"
            "Token tugadi va avtomatik login ham ishlamadi.\n"
            "Sabablari: parol o'zgargan, hisob bloklangan yoki server javob bermayapti.\n\n"
            "Render → Environment'da POS_ORG_ID, POS_LOGIN, POS_PASSWORD ni tekshiring."
        )
    return (
        "🔑 Token yangilash kerak, qayta login qiling.\n\n"
        "Avtomatik login sozlanmagan. Buni bir marta sozlab qo'ysangiz, bot tokenni "
        "o'zi yangilab turadi:\n"
        "Render → Environment → POS_ORG_ID, POS_LOGIN, POS_PASSWORD."
    )


def format_token_warning(days_left: float, expiry: datetime) -> str:
    return (
        f"⚠️ Diqqat: POS token {days_left:.0f} kundan keyin tugaydi\n"
        f"🕒 Tugash vaqti: {expiry.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"Token tugasa sotuv xabarlari to'xtaydi.\n"
        f"POS tizimiga kirib yangi token oling va Render'da ACCESS_TOKEN / "
        f"REFRESH_TOKEN ni yangilang."
    )


# ------------------------------------------------------------------ grafik --


def build_chart(state: State) -> bytes | None:
    """Oxirgi 7 kunlik sotuv diagrammasi (PNG). Bloklovchi — thread'da chaqiriladi."""
    try:
        import matplotlib
        matplotlib.use("Agg")            # displaysiz serverda ishlashi uchun
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    days = state.last_days(7)
    labels = [fmt_date(d) for d, _ in days]
    values = [s["qty"] for _, s in days]

    if not any(values):
        return None

    fig, ax = plt.subplots(figsize=(8, 4), dpi=130)
    bars = ax.bar(labels, values, color="#8B5E3C", width=0.6)

    ax.set_title("Dark Chocolate — 7 kunlik sotuv", fontsize=13, weight="bold", pad=14)
    ax.set_ylabel("dona")
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linestyle="--")
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", rotation=30, labelsize=9)

    for bar, value in zip(bars, values):
        if value:
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{int(value)}", ha="center", va="bottom", fontsize=9)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
