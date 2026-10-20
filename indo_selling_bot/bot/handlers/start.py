"""
Start and Help handlers for aiogram 3 Telegram Bot.
Configures persistent 4-button menu with server-side administrator authorization.
"""
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from indo_selling_bot.config import settings
from indo_selling_bot.bot.keyboards import get_main_reply_keyboard
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.wallet_service import WalletService

start_router = Router()

@start_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Send welcome greeting and green Buy Indo inline button."""
    user = message.from_user
    user_id = user.id if user else 0

    # Register/update customer in DB and fetch wallet balance
    wallet_balance = 0.0
    if user:
        async with AsyncSessionLocal() as db:
            await WalletService.get_or_create_customer(
                db=db,
                telegram_id=user.id,
                username=user.username,
                first_name=user.first_name
            )
            wallet_balance = await WalletService.get_balance(db, user.id)
            await db.commit()

    user_name = user.first_name if user and user.first_name else "User"

    welcome_text = (
        f"👋 Welcome Legend Indo Bot, {user_name}\n\n"
        "Cheapest Indo in Market : 🛒  ₹ 7 /- per\n\n"
        "🪙 Old Igs With Best Quality \n"
        f"👛 My Wallet:  ₹{wallet_balance:,.2f}\n\n"
        "Owner : @LEGEND_TG "
    )

    start_inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo", style="success")]
    ])

    await message.answer(
        welcome_text,
        reply_markup=start_inline_kb
    )


@start_router.message(Command("owner"))
async def handle_owner_command(message: Message) -> None:
    """Send owner contact information."""
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Contact Owner", url="https://t.me/LEGEND_TG")]
    ])
    await message.answer("Owner : @LEGEND_TG", reply_markup=kb)

@start_router.message(Command("help"))
async def handle_help(message: Message) -> None:
    """Send help instructions, available commands, and stock import format."""
    user = message.from_user
    user_id = user.id if user else 0
    is_admin = settings.is_admin(user_id)

    help_lines = [
        "💡 *Indo Commerce Bot Commands:*",
        "",
        "• `/start` - Launch or refresh main menu",
        "• `/catalog` or `🛒 Buy Indo` - Browse 20 age categories and purchase items",
        "• `/stock` or `📦 Total Stock` - View day-wise stock quantities (Day 1 - Day 20)",
        "• `/addfund` or `💰 Add Fund` - Top up your wallet balance",
        "• `/orders` - Check status of your recent orders",
        "• `/help` - View this help message",
    ]

    if is_admin:
        help_lines.extend([
            "",
            "🔐 *Administrator Stock Import Formats:*",
            "Command: `/admin` or `/import_stock`",
            "",
            "• *Format 1 (Full):*",
            "  `ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS`",
            "  _Example:_ `DEMO-0001|DAY_1_OLD|2026-10-18T12:00:00Z|AVAILABLE`",
            "",
            "• *Format 2 (Compact — Auto-Aged):*",
            "  `ITEM_ID|CREATED_AT_UTC`",
            "  _Example:_ `DEMO-0002|2026-10-17T15:30:00Z`",
            "",
            "📝 *Sample TXT File Content:*",
            "```text",
            "# Inventory batch upload sample",
            "ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS",
            "DEMO-0001|DAY_1_OLD|2026-10-18T12:00:00Z|AVAILABLE",
            "DEMO-0002|DAY_2_OLD|2026-10-17T10:00:00Z|AVAILABLE",
            "DEMO-0003|2026-10-16T14:30:00Z",
            "```",
        ])

    help_lines.extend([
        "",
        "🔒 *Security Reminder:* All orders are manually verified by authorized administrators. "
        "Never share sensitive personal credentials or banking passwords."
    ])

    await message.answer("\n".join(help_lines), parse_mode="Markdown")

@start_router.callback_query(F.data == "back_to_main")
async def handle_back_to_main_callback(query: CallbackQuery) -> None:
    """Callback returning customer to main dashboard message."""
    await query.answer()
    user = query.from_user
    user_id = user.id if user else 0

    wallet_balance = 0.0
    if user:
        async with AsyncSessionLocal() as db:
            wallet_balance = await WalletService.get_balance(db, user.id)

    user_name = user.first_name if user and user.first_name else "User"
    welcome_text = (
        f"👋 Welcome Legend Indo Bot, {user_name}\n\n"
        "Cheapest Indo in Market : 🛒  ₹ 7 /- per\n\n"
        "🪙 Old Igs With Best Quality \n"
        f"👛 My Wallet:  ₹{wallet_balance:,.2f}\n\n"
        "Owner : @LEGEND_TG "
    )

    start_inline_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo", style="success")]
    ])

    if query.message:
        try:
            await query.message.edit_text(
                welcome_text,
                reply_markup=start_inline_kb
            )
        except Exception:
            await query.message.answer(
                welcome_text,
                reply_markup=start_inline_kb
            )
