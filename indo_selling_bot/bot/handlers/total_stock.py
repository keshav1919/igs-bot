"""
Total Stock Telegram Flow.
Displays day-wise inventory summary from Day 1 to Day 20 formatted as a clean ASCII table.
Enforces:
- Reads stock directly from stock.txt (source of truth).
- Days with 0 stock are hidden ("that days have 0 stock dont show them").
- Never exposes private inventory payloads or credentials.
"""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from indo_selling_bot.services.stock_txt_service import StockTxtService

total_stock_router = Router()

def _format_stock_summary(summary: dict) -> str:
    """
    Format live inventory summary as an ASCII table matching user specification.
    Only displays days that have stock > 0.
    """
    tz = ZoneInfo("Asia/Kolkata")
    today = datetime.now(tz).date()

    rows = []
    for cat in summary.get("categories", []):
        qty = cat["quantity"]
        if qty <= 0:
            continue  # "that days have 0 stock dont show them"

        day_num = cat["day"]
        if cat.get("dates") and len(cat["dates"]) > 0:
            try:
                dt = datetime.strptime(cat["dates"][0], "%Y-%m-%d").date()
            except Exception:
                dt = today - timedelta(days=day_num)
        else:
            dt = today - timedelta(days=day_num)

        diff_days = (today - dt).days
        if diff_days <= 0:
            period = "Today"
        elif diff_days == 1:
            period = "1 day old"
        else:
            period = f"{diff_days} days old"
        date_str = dt.strftime("%d %b")
        rows.append(f"│ {period:<12} │ {date_str:<7} │ {qty:>5} │")

    total_stock = summary.get("total_stock", 0)

    table_lines = [
        "┌──────────────┬─────────┬───────┐",
        "│ Period       │ Date    │ Stock │",
    ]

    if rows:
        table_lines.append("├──────────────┼─────────┼───────┤")
        table_lines.extend(rows)
        table_lines.append("├──────────────┴─────────┼───────┤")
    else:
        table_lines.append("├──────────────┴─────────┼───────┤")

    total_label = "Total"
    table_lines.append(f"│ {total_label:<22} │ {total_stock:>5} │")
    table_lines.append("└────────────────────────┴───────┘")

    table_text = "\n".join(table_lines)
    return f"📅 *Daily Breakdown:*\n```\n{table_text}\n```"

def _get_stock_actions_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Refresh Stock", callback_data="refresh_total_stock", style="primary"),
            InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo", style="success")
        ],
        [
            InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")
        ]
    ])

@total_stock_router.message(F.text == "📦 Total Stock")
@total_stock_router.message(Command("stock"))
async def handle_total_stock_menu(message: Message) -> None:
    """Send live day-wise stock summary from stock.txt."""
    summary = StockTxtService.get_total_stock_summary(max_days=20)
    text = _format_stock_summary(summary)
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=_get_stock_actions_keyboard()
    )

@total_stock_router.callback_query(F.data == "refresh_total_stock")
async def handle_refresh_stock_callback(query: CallbackQuery) -> None:
    """Refresh the live stock display in place from stock.txt."""
    await query.answer("Stock refreshed!")
    summary = StockTxtService.get_total_stock_summary(max_days=20)
    text = _format_stock_summary(summary)
    if query.message:
        try:
            await query.message.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=_get_stock_actions_keyboard()
            )
        except Exception:
            pass
