"""
Outlook & Hotmail Store, Inbox Reader, and OTP Verification Bot Handlers.
Integrated with unified wallet balance and automatic stock management.
"""
import asyncio
import datetime
import html
import logging
import math
from typing import Dict, Any, List, Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.outlook_service import OutlookService
from indo_selling_bot.services.mail_service import (
    mail_service,
    parse_all_credentials,
    clean_html_to_readable_text,
    format_relative_time,
    format_email_date
)
from indo_selling_bot.bot.keyboards import (
    get_outlook_accounts_keyboard,
    get_outlook_buy_confirm_keyboard,
    get_outlook_purchase_success_keyboard,
    get_outlook_inbox_keyboard,
    get_outlook_full_email_keyboard,
    get_outlook_delete_confirm_keyboard
)

logger = logging.getLogger("outlook_handlers")
outlook_router = Router()

ACCOUNTS_PAGE_SIZE = 8
INBOX_PAGE_SIZE = 5

# In-memory cache for recent messages per account: {account_id: list_of_messages}
ACCOUNT_MESSAGES_CACHE: Dict[int, List[Dict[str, Any]]] = {}


def render_inbox_view(
    email: str,
    messages: List[Dict[str, Any]],
    page: int = 1,
    latency_ms: int = 0,
    from_cache: bool = False
) -> tuple[str, int, int, int]:
    """Renders the account inbox view with email numbers and timestamps."""
    total_messages = len(messages)
    total_pages = max(1, math.ceil(total_messages / INBOX_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * INBOX_PAGE_SIZE
    end_idx = min(start_idx + INBOX_PAGE_SIZE, total_messages)
    page_messages = messages[start_idx:end_idx]

    clean_email = html.escape(email)
    header = f"📩 <b>{clean_email}</b> · {total_messages} · {page}/{total_pages}\n"
    lines = [header]

    if not messages:
        lines.append("📭 <i>Inbox is empty.</i>")
    else:
        for offset, m in enumerate(page_messages):
            msg_num = start_idx + offset + 1
            subj = html.escape(m.get("subject", "No subject"))
            code = m.get("code")
            date_val = m.get("created") or m.get("date")
            rel_time = format_relative_time(date_val)

            if code and code != "------":
                lines.append(
                    f"🔑 {msg_num}. {subj} · <code>{html.escape(code)}</code> · {rel_time}"
                )
            else:
                lines.append(
                    f"📩 {msg_num}. {subj} · {rel_time}"
                )

    lines.append("")
    now_str = datetime.datetime.now().strftime("%H:%M")
    lines.append(f"🔄 <i>Refreshed {now_str}</i>")
    if from_cache:
        lines.append("🗄️ <i>Loaded from cache.</i>")
    elif latency_ms > 0:
        lines.append(f"⚡ <i>{latency_ms}ms — loaded via API.</i>")
    else:
        lines.append("⚡ <i>Loaded via API.</i>")

    items_count = len(page_messages)
    start_num = start_idx + 1
    return "\n".join(lines), start_num, items_count, total_pages


def render_full_email(
    m: Dict[str, Any],
    account_email: str
) -> tuple[str, Optional[str]]:
    """Renders the detailed single email view."""
    subj = html.escape(m.get("subject", "No subject"))
    sender = html.escape(m.get("senderName") or m.get("from") or "Mail")
    date_val = m.get("created") or m.get("date")
    fmt_date = format_email_date(date_val)
    code = m.get("code") if m.get("code") != "------" else None

    raw_body = m.get("message") or m.get("preview") or m.get("snippet") or ""
    clean_body = clean_html_to_readable_text(raw_body)
    clean_body_html = html.escape(clean_body)

    lines = [
        f"<b>{subj}</b>",
        f"👤 {sender} · 🕒 {fmt_date}",
        ""
    ]

    if code:
        lines.append(f"🔐 <code>{html.escape(code)}</code>")
        lines.append("")

    lines.append(clean_body_html or "<i>(No body content)</i>")
    lines.append("")
    size_kb = round(len(raw_body) / 1024, 3)
    lines.append(f"⚡ {size_kb} KB · 🆓 <i>free</i>")

    return "\n".join(lines), code


async def show_outlook_screen(user_id: int, page: int, message_or_call, edit: bool = False):
    """Displays user accounts, available Outlook stock, and buy button."""
    accounts, total_count = await OutlookService.get_user_accounts(
        user_id=user_id,
        page=page,
        page_size=ACCOUNTS_PAGE_SIZE
    )
    stock_count = await OutlookService.get_available_stock_count()

    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)

    total_pages = max(1, math.ceil(total_count / ACCOUNTS_PAGE_SIZE))
    page = max(1, min(page, total_pages))

    text = (
        f"📁 <b>Your Outlook accounts ({total_count})</b>\n"
        "━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Stock:</b> {stock_count} available\n"
        f"💰 <b>Price:</b> ₹{OutlookService.PRICE_PER_ACCOUNT:.2f} / email\n"
        f"💳 <b>Your Balance:</b> ₹{balance:.2f}"
    )

    if total_count == 0:
        text += "\n\n<i>You haven't bought any accounts yet. Tap below to buy!</i>"

    keyboard = get_outlook_accounts_keyboard(
        accounts=accounts,
        current_page=page,
        total_pages=total_pages,
        stock_count=stock_count
    )

    if edit and isinstance(message_or_call, CallbackQuery):
        try:
            await message_or_call.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await message_or_call.message.answer(text, reply_markup=keyboard)
    elif isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.answer(text, reply_markup=keyboard)
    else:
        await message_or_call.answer(text, reply_markup=keyboard)


# ==========================================
# ENTRY POINTS
# ==========================================

@outlook_router.message(F.text.in_(["📩 Outlook", "📧 Outlook", "📬 My Emails"]))
@outlook_router.message(Command("outlook"))
async def handle_outlook_entry(message: Message):
    """Entry point from reply keyboard or command."""
    await show_outlook_screen(user_id=message.from_user.id, page=1, message_or_call=message, edit=False)


@outlook_router.callback_query(F.data.in_(["outlook_home", "menu_home"]))
async def handle_outlook_home_callback(callback: CallbackQuery):
    """Entry point from inline button."""
    await callback.answer()
    await show_outlook_screen(user_id=callback.from_user.id, page=1, message_or_call=callback, edit=True)


@outlook_router.callback_query(F.data.startswith("acc_page:"))
async def handle_acc_page(callback: CallbackQuery):
    """Pagination for user accounts."""
    page = int(callback.data.split(":")[1])
    await callback.answer()
    await show_outlook_screen(user_id=callback.from_user.id, page=page, message_or_call=callback, edit=True)


# ==========================================
# BUYING FLOW
# ==========================================

@outlook_router.callback_query(F.data == "buy_confirm")
@outlook_router.message(F.text == "🛒 Buy Outlook (₹3)")
async def handle_buy_confirm(event: Message | CallbackQuery):
    """Purchase confirmation screen."""
    user = event.from_user
    user_id = user.id

    stock_count = await OutlookService.get_available_stock_count()
    if stock_count <= 0:
        msg = "❌ <b>Out of stock!</b>\nPlease wait for the admin to restock."
        if isinstance(event, CallbackQuery):
            await event.answer("❌ Out of stock! Please wait for restock.", show_alert=True)
        else:
            await event.answer(msg)
        return

    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)

    price = OutlookService.PRICE_PER_ACCOUNT
    can_afford = balance >= price

    if not can_afford:
        text = (
            "⚠️ <b>Insufficient Balance!</b>\n\n"
            f"💰 <b>Account Price:</b> ₹{price:.2f}\n"
            f"💳 <b>Your Balance:</b> ₹{balance:.2f}\n"
            f"❌ <i>You need ₹{price - balance:.2f} more to purchase.</i>\n\n"
            "Please recharge your wallet via 💰 Add Fund."
        )
        keyboard = get_outlook_buy_confirm_keyboard(can_afford=False)
    else:
        text = (
            "🛒 <b>Confirm Purchase</b>\n\n"
            "📦 <b>Item:</b> 1x Outlook Account\n"
            "⚡ <b>Feature:</b> Instant Verification Code & OTP Reader\n"
            f"💰 <b>Price:</b> ₹{price:.2f}\n"
            f"💳 <b>Your Balance:</b> ₹{balance:.2f} ➔ <b>₹{balance - price:.2f}</b>\n\n"
            "Tap <b>Confirm & Buy</b> to receive your email instantly:"
        )
        keyboard = get_outlook_buy_confirm_keyboard(can_afford=True)

    if isinstance(event, CallbackQuery):
        await event.answer()
        try:
            await event.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            await event.message.answer(text, reply_markup=keyboard)
    else:
        await event.answer(text, reply_markup=keyboard)


@outlook_router.callback_query(F.data == "buy_execute")
async def handle_buy_execute(callback: CallbackQuery):
    """Executes the purchase and delivers credentials."""
    user = callback.from_user
    user_id = user.id

    ok, reason, account = await OutlookService.buy_outlook_account(
        user_id=user_id,
        price=OutlookService.PRICE_PER_ACCOUNT
    )

    if not ok or not account:
        if reason == "OUT_OF_STOCK":
            await callback.answer("❌ Out of stock!", show_alert=True)
            await show_outlook_screen(user_id=user_id, page=1, message_or_call=callback, edit=True)
        elif reason == "INSUFFICIENT_BALANCE":
            await callback.answer("❌ Insufficient balance! Please recharge via Add Fund.", show_alert=True)
        else:
            await callback.answer(f"❌ Purchase failed: {reason}", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        new_balance = await WalletService.get_balance(db, user_id)
    stock_left = await OutlookService.get_available_stock_count()

    await callback.answer("🎉 Purchase successful!", show_alert=False)

    text = (
        "🎉 <b>Purchase Successful!</b>\n\n"
        f"📧 <b>Email:</b> <code>{html.escape(account['email'])}</code>\n"
        f"🔑 <b>Password:</b> <code>{html.escape(account['password'])}</code>\n"
        f"💰 <b>Price Paid:</b> ₹{OutlookService.PRICE_PER_ACCOUNT:.2f}\n"
        f"💳 <b>Wallet Balance:</b> ₹{new_balance:.2f}\n\n"
        "<i>Tap below to open your inbox & receive OTP codes instantly:</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=get_outlook_purchase_success_keyboard(account_id=account["id"])
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=get_outlook_purchase_success_keyboard(account_id=account["id"])
        )

    # Notify admins
    buyer_name = html.escape(user.full_name or "Unknown")
    buyer_username = f"@{user.username}" if user.username else "No username"
    admin_alert = (
        "🔔 <b>New Outlook Purchase Alert!</b>\n\n"
        f"👤 <b>Buyer:</b> {buyer_name} ({buyer_username})\n"
        f"🆔 <b>Buyer ID:</b> <code>{user.id}</code>\n"
        f"📧 <b>Account:</b> <code>{html.escape(account.get('email', ''))}</code>\n"
        f"🔑 <b>Password:</b> <code>{html.escape(account.get('password', ''))}</code>\n"
        f"💰 <b>Paid:</b> ₹{OutlookService.PRICE_PER_ACCOUNT:.2f}\n"
        f"💳 <b>Wallet Balance Left:</b> ₹{new_balance:.2f}\n"
        f"📦 <b>Outlook Stock Left:</b> <b>{stock_left}</b>"
    )
    for admin_id in settings.admin_ids:
        try:
            await callback.bot.send_message(admin_id, admin_alert)
        except Exception:
            pass


@outlook_router.callback_query(F.data == "funds_help")
async def handle_funds_help(callback: CallbackQuery):
    """Directs user to add funds."""
    await callback.answer()
    text = (
        "💳 <b>How to Add Funds</b>\n\n"
        "You can easily recharge your wallet using the <b>💰 Add Fund</b> button in the main menu.\n\n"
        f"🆔 <b>Your Telegram ID:</b> <code>{callback.from_user.id}</code>\n"
        f"💰 <b>Outlook Price:</b> ₹{OutlookService.PRICE_PER_ACCOUNT:.2f} / email\n\n"
        "Scan the dynamic UPI QR code, complete payment, and upload your screenshot for verification."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Go to Add Fund", callback_data="menu_add_fund", style="success")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="outlook_home", style="primary")]
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        await callback.message.answer(text, reply_markup=kb)


# ==========================================
# INBOX & MESSAGE VIEWING
# ==========================================

@outlook_router.callback_query(F.data.startswith("acc_view:"))
async def handle_acc_view(callback: CallbackQuery):
    """Opens inbox of an owned account."""
    parts = callback.data.split(":")
    acc_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    user_id = callback.from_user.id

    acc = await OutlookService.get_account_by_id(account_id=acc_id, user_id=user_id)
    if not acc:
        await callback.answer("Account not found or expired.", show_alert=True)
        return

    await callback.answer("Loading inbox...", show_alert=False)

    cached_messages = ACCOUNT_MESSAGES_CACHE.get(acc_id)
    if cached_messages:
        text, start_num, items_count, total_pages = render_inbox_view(
            email=acc["email"],
            messages=cached_messages,
            page=page,
            from_cache=True
        )
        keyboard = get_outlook_inbox_keyboard(
            account_id=acc_id,
            start_num=start_num,
            items_count=items_count,
            current_page=page,
            total_pages=total_pages
        )
        try:
            await callback.message.edit_text(text, reply_markup=keyboard)
        except Exception:
            pass
        return

    fetch_res = await mail_service.get_latest_code(acc)
    messages = fetch_res.get("messages", [])
    latency_ms = fetch_res.get("latency_ms", 0)
    ACCOUNT_MESSAGES_CACHE[acc_id] = messages

    if fetch_res.get("code") and fetch_res.get("code") != "------":
        await OutlookService.update_account_last_code(acc_id, fetch_res["code"])

    text, start_num, items_count, total_pages = render_inbox_view(
        email=acc["email"],
        messages=messages,
        page=page,
        latency_ms=latency_ms,
        from_cache=False
    )
    keyboard = get_outlook_inbox_keyboard(
        account_id=acc_id,
        start_num=start_num,
        items_count=items_count,
        current_page=page,
        total_pages=total_pages
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass


@outlook_router.callback_query(F.data.startswith("acc_refresh:"))
async def handle_acc_refresh(callback: CallbackQuery):
    """Forces refreshing the inbox via live API call."""
    parts = callback.data.split(":")
    acc_id = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 1
    user_id = callback.from_user.id

    acc = await OutlookService.get_account_by_id(account_id=acc_id, user_id=user_id)
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    await callback.answer("Refreshing inbox...", show_alert=False)

    fetch_res = await mail_service.get_latest_code(acc)
    messages = fetch_res.get("messages", [])
    latency_ms = fetch_res.get("latency_ms", 0)
    ACCOUNT_MESSAGES_CACHE[acc_id] = messages

    if fetch_res.get("code") and fetch_res.get("code") != "------":
        await OutlookService.update_account_last_code(acc_id, fetch_res["code"])

    text, start_num, items_count, total_pages = render_inbox_view(
        email=acc["email"],
        messages=messages,
        page=page,
        latency_ms=latency_ms,
        from_cache=False
    )
    keyboard = get_outlook_inbox_keyboard(
        account_id=acc_id,
        start_num=start_num,
        items_count=items_count,
        current_page=page,
        total_pages=total_pages
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass


@outlook_router.callback_query(F.data.startswith("inbox_p:"))
async def handle_inbox_pagination(callback: CallbackQuery):
    """Paginates through inbox emails."""
    parts = callback.data.split(":")
    acc_id = int(parts[1])
    page = int(parts[2])
    user_id = callback.from_user.id

    acc = await OutlookService.get_account_by_id(account_id=acc_id, user_id=user_id)
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    messages = ACCOUNT_MESSAGES_CACHE.get(acc_id)
    if not messages:
        fetch_res = await mail_service.get_latest_code(acc)
        messages = fetch_res.get("messages", [])
        ACCOUNT_MESSAGES_CACHE[acc_id] = messages

    text, start_num, items_count, total_pages = render_inbox_view(
        email=acc["email"],
        messages=messages,
        page=page,
        from_cache=True
    )
    keyboard = get_outlook_inbox_keyboard(
        account_id=acc_id,
        start_num=start_num,
        items_count=items_count,
        current_page=page,
        total_pages=total_pages
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@outlook_router.callback_query(F.data.startswith("msg_read:"))
async def handle_msg_read(callback: CallbackQuery):
    """Renders single email details with copyable OTP."""
    parts = callback.data.split(":")
    acc_id = int(parts[1])
    msg_idx = int(parts[2])
    user_id = callback.from_user.id

    acc = await OutlookService.get_account_by_id(account_id=acc_id, user_id=user_id)
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    messages = ACCOUNT_MESSAGES_CACHE.get(acc_id)
    if not messages or msg_idx >= len(messages):
        fetch_res = await mail_service.get_latest_code(acc)
        messages = fetch_res.get("messages", [])
        ACCOUNT_MESSAGES_CACHE[acc_id] = messages

    if not messages or msg_idx >= len(messages):
        await callback.answer("Message expired or not found.", show_alert=True)
        return

    await callback.answer()
    m = messages[msg_idx]
    text, code = render_full_email(
        m=m,
        account_email=acc["email"]
    )
    keyboard = get_outlook_full_email_keyboard(account_id=acc_id, code=code)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard)
    except Exception:
        pass


# ==========================================
# REMOVE & AUTO-RESTOCK
# ==========================================

@outlook_router.callback_query(F.data.startswith("acc_del_ask:"))
async def handle_del_ask(callback: CallbackQuery):
    """Asks for confirmation to remove and restock account."""
    acc_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id
    acc = await OutlookService.get_account_by_id(account_id=acc_id, user_id=user_id)
    if not acc:
        await callback.answer("Account not found.", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑️ <b>Remove & Restock</b> <code>{html.escape(acc['email'])}</code>?\n\n"
        "<i>This email will be removed from your accounts and returned back to store inventory.</i>",
        reply_markup=get_outlook_delete_confirm_keyboard(account_id=acc_id)
    )
    await callback.answer()


@outlook_router.callback_query(F.data.startswith("acc_del_do:"))
async def handle_del_do(callback: CallbackQuery):
    """Executes account restocking."""
    acc_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    restocked = await OutlookService.restock_account(account_id=acc_id, user_id=user_id)
    ACCOUNT_MESSAGES_CACHE.pop(acc_id, None)

    if restocked:
        await callback.answer("✅ Removed and returned to stock.", show_alert=True)
    else:
        await callback.answer("Could not remove account.", show_alert=True)

    await show_outlook_screen(user_id=user_id, page=1, message_or_call=callback, edit=True)


@outlook_router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()


# ==========================================
# ADMIN CREDENTIAL IMPORT (PIPE-SEPARATED)
# ==========================================

@outlook_router.message(F.text.contains("|"))
async def handle_credential_input(message: Message):
    """Allows admins to paste Outlook credentials to add to stock."""
    user_id = message.from_user.id
    if not settings.is_admin(user_id):
        # Non-admins get store prompt
        await message.answer(
            "🛒 <b>Outlook Store</b>\n\n"
            "To purchase an Outlook email, tap <b>📩 Outlook</b> below.\n"
            "Only administrators can add inventory stock."
        )
        return

    raw_text = message.text.strip()
    creds = parse_all_credentials(raw_text)
    if not creds:
        await message.answer(
            "⚠️ <b>Invalid format!</b>\n"
            "Use: <code>email|password|refresh_token|client_id</code>"
        )
        return

    added, skipped = await OutlookService.add_stock_bulk(creds, price=OutlookService.PRICE_PER_ACCOUNT)
    total_stock = await OutlookService.get_available_stock_count()

    await message.answer(
        f"✅ <b>Outlook Stock Added Successfully!</b>\n\n"
        f"📦 Accounts Added: <b>{added}</b>\n"
        f"⚠️ Skipped / Duplicates: <b>{skipped}</b>\n"
        f"📊 Total Available Outlook Stock: <b>{total_stock}</b>\n"
        f"💰 Price per Account: ₹{OutlookService.PRICE_PER_ACCOUNT:.2f}"
    )


@outlook_router.message(Command("outlook_stock"))
@outlook_router.message(Command("stock_outlook"))
async def handle_outlook_stock_cmd(message: Message):
    """Quick command to view Outlook stock."""
    stock = await OutlookService.get_available_stock_count()
    sold = await OutlookService.get_sold_stock_count()
    await message.answer(
        f"📦 <b>Outlook Stock Overview</b>\n\n"
        f"• Available Stock: <b>{stock}</b>\n"
        f"• Total Sold: <b>{sold}</b>\n"
        f"• Price: <b>₹{OutlookService.PRICE_PER_ACCOUNT:.2f}</b>"
    )
