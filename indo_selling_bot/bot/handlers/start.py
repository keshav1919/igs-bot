"""
Start and Help handlers for aiogram 3 Telegram Bot.
Configures persistent 4-button menu with server-side administrator authorization.
"""
from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from indo_selling_bot.config import settings
from indo_selling_bot.bot.keyboards import (
    get_main_reply_keyboard,
    get_channel_verification_keyboard,
    get_start_welcome_keyboard,
    CHANNEL_VERIFICATION_TEXT,
)
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.channel_service import ChannelService
from indo_selling_bot.services.free_claim_service import FreeClaimService

start_router = Router()

@start_router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Send channel verification if unverified, or welcome greeting with Buy and Free Indo buttons."""
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

    # Check channel subscription
    is_sub = await ChannelService.is_user_subscribed(message.bot, user_id)
    is_admin = settings.is_admin(user_id)

    # Always ensure the persistent quick access reply keyboard (4-dot grid button) is active
    try:
        await message.answer(
            "⚡ <i>Quick Access Menu:</i>",
            reply_markup=get_main_reply_keyboard(is_admin=is_admin),
            parse_mode="HTML"
        )
    except Exception:
        pass

    if not is_sub:
        await message.answer(
            CHANNEL_VERIFICATION_TEXT,
            reply_markup=get_channel_verification_keyboard(),
            parse_mode="HTML"
        )
        return

    user_name = user.first_name if user and user.first_name else "User"

    welcome_text = (
        f"👋 Welcome Legend Indo Bot, {user_name}\n\n"
        "Cheapest Indo in Market : 🛒  ₹ 7 /- per\n\n"
        "🪙 Old Igs With Best Quality \n"
        f"👛 My Wallet:  ₹{wallet_balance:,.2f}\n\n"
        "Owner : @LEGEND_TG "
    )

    await message.answer(
        welcome_text,
        reply_markup=get_start_welcome_keyboard()
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

    # Check channel subscription
    is_sub = await ChannelService.is_user_subscribed(query.bot, user_id)
    if not is_sub:
        if query.message:
            try:
                await query.message.edit_text(
                    CHANNEL_VERIFICATION_TEXT,
                    reply_markup=get_channel_verification_keyboard(),
                    parse_mode="HTML"
                )
            except Exception:
                await query.message.answer(
                    CHANNEL_VERIFICATION_TEXT,
                    reply_markup=get_channel_verification_keyboard(),
                    parse_mode="HTML"
                )
        return

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

    if query.message:
        try:
            await query.message.edit_text(
                welcome_text,
                reply_markup=get_start_welcome_keyboard()
            )
        except Exception:
            await query.message.answer(
                welcome_text,
                reply_markup=get_start_welcome_keyboard()
            )

        is_admin = settings.is_admin(user_id)
        try:
            await query.message.answer(
                "⚡ <i>Quick Access Menu:</i>",
                reply_markup=get_main_reply_keyboard(is_admin=is_admin),
                parse_mode="HTML"
            )
        except Exception:
            pass

@start_router.callback_query(F.data == "verify_channel")
async def handle_verify_channel_callback(query: CallbackQuery) -> None:
    """Check if user joined channel; unlock bot or show warning alert."""
    user = query.from_user
    user_id = user.id if user else 0

    is_sub = await ChannelService.is_user_subscribed(query.bot, user_id)
    if not is_sub:
        await query.answer("❌ Oii Chanel join kr chup Chap 🫵", show_alert=True)
        return

    await query.answer("✅ Verified!", show_alert=False)

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

    if query.message:
        try:
            await query.message.edit_text(
                welcome_text,
                reply_markup=get_start_welcome_keyboard()
            )
        except Exception:
            await query.message.answer(
                welcome_text,
                reply_markup=get_start_welcome_keyboard()
            )

        is_admin = settings.is_admin(user_id)
        try:
            await query.message.answer(
                "⚡ <i>Quick Access Menu:</i>",
                reply_markup=get_main_reply_keyboard(is_admin=is_admin),
                parse_mode="HTML"
            )
        except Exception:
            pass

@start_router.callback_query(F.data == "claim_free_ig")
async def handle_claim_free_ig_callback(query: CallbackQuery) -> None:
    """Claim strictly 1 free Indo IG for verified user."""
    user = query.from_user
    if not user:
        await query.answer("❌ User not found.", show_alert=True)
        return

    # Check channel subscription first
    is_sub = await ChannelService.is_user_subscribed(query.bot, user.id)
    if not is_sub:
        await query.answer("❌ Oii Chanel join kr chup Chap 🫵", show_alert=True)
        return

    # Attempt claim
    ok, msg, account = await FreeClaimService.claim_free_ig(
        telegram_user_id=user.id,
        username=user.username
    )

    if not ok:
        await query.answer(msg, show_alert=True)
        return

    await query.answer("✅ Free Indo claimed!", show_alert=False)

    email = account.get("email", "")
    password = account.get("password", "")
    username = account.get("username", "")

    delivery_text = (
        "🎁 <b>Your Free Indo IG:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <code>{email}:{password}</code>\n"
    )
    if username:
        delivery_text += f"👤 <code>{username}</code>\n"
    delivery_text += (
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Enjoy your Indo! 🎉"
    )

    if query.message:
        await query.message.answer(delivery_text, parse_mode="HTML")

    # Notify admins of claim
    admin_notify = (
        "🎁 <b>FREE INDO IG CLAIMED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: @{user.username or 'NoUser'} (<code>{user.id}</code>)\n"
        f"📧 Account: <code>{email}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    for admin_id in settings.admin_ids:
        try:
            await query.bot.send_message(chat_id=admin_id, text=admin_notify, parse_mode="HTML")
        except Exception:
            pass
