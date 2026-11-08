"""
Administrative Handlers for Free Indo IGS Feature.
Includes full feature settings configuration, tutorial video management,
submission logs inspection, pending reward processing, user timeout management,
and stored proxy retrieval with single and batch CopyTextButton support.
"""
import html
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.free_indo_service import FreeIndoService
from indo_selling_bot.bot.keyboards import (
    get_admin_free_indo_keyboard,
    get_admin_free_indo_settings_keyboard,
    get_admin_free_indo_tutorial_keyboard,
    get_admin_stored_proxies_menu_keyboard,
    get_admin_retrieved_proxies_keyboard,
    get_admin_pending_rewards_keyboard,
    get_admin_timeout_users_keyboard,
)

logger = logging.getLogger("admin_free_indo")

admin_free_indo_router = Router()


class AdminFreeIndoState(StatesGroup):
    waiting_for_host = State()
    waiting_for_port = State()
    waiting_for_timeout = State()
    waiting_for_max_fails = State()
    waiting_for_timeout_mins = State()
    waiting_for_tutorial_video = State()
    waiting_for_custom_proxy_amount = State()
    waiting_for_user_search = State()


def is_admin_check(user_id: int | None, username: str | None = None) -> bool:
    if not user_id:
        return False
    return settings.is_admin(user_id, username)


# =========================================================================
# Main Free Indo Admin Dashboard
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo")
@admin_free_indo_router.message(Command("admin_free_indo"))
async def handle_admin_free_indo_dashboard(event: Message | CallbackQuery, state: FSMContext | None = None) -> None:
    """Administrator dashboard overview for Free Indo IGS."""
    user = event.from_user
    user_id = user.id if user else 0
    if not is_admin_check(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("🚫 Unauthorized: Admin access required.", show_alert=True)
        else:
            await event.answer("🚫 Unauthorized: Admin access required.")
        return

    if state:
        await state.clear()

    if isinstance(event, CallbackQuery):
        await event.answer()

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        stats = await FreeIndoService.get_proxy_statistics(db)

    status_str = "🟢 Active" if cfg.enabled else "🔴 Disabled"

    text = (
        "🎁 <b>Free Indo IGS — Admin Control Panel</b>\n\n"
        f"<b>Status:</b> {status_str}\n"
        f"<b>Proxy Target:</b> <code>{cfg.proxy_host}:{cfg.proxy_port}</code> (HTTP)\n"
        f"<b>Timeout:</b> {cfg.connection_timeout}s | <b>Failure Limit:</b> {cfg.max_failed_attempts} fails / {cfg.timeout_minutes}m\n\n"
        "📊 <b>Feature KPI Overview:</b>\n"
        f"• Total Submissions: <b>{stats['total_submissions']}</b>\n"
        f"• Total Stored Proxies: <b>{stats['total_stored']}</b>\n"
        f"• Available Proxies: <b>{stats['available_proxies']}</b>\n"
        f"• Retrieved Proxies: <b>{stats['retrieved_proxies']}</b>\n"
        f"• Total Rewards Delivered: <b>{stats['total_rewards_delivered']}</b>\n"
        f"• Pending Rewards Queue: <b>{stats['pending_rewards']}</b>\n\n"
        "Select a section below to manage configuration, review submissions, or export proxies:"
    )

    kb = get_admin_free_indo_keyboard(enabled=cfg.enabled)

    if isinstance(event, CallbackQuery) and event.message:
        try:
            await event.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await event.message.answer(text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(event, Message):
        await event.answer(text, reply_markup=kb, parse_mode="HTML")


# =========================================================================
# Settings & Configuration Handlers
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo:settings")
async def handle_admin_settings_view(query: CallbackQuery) -> None:
    """View and adjust settings."""
    if not is_admin_check(query.from_user.id):
        await query.answer("Access denied.", show_alert=True)
        return
    await query.answer()

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)

    text = (
        "⚙️ <b>Free Indo IGS Settings Configuration</b>\n\n"
        f"• Feature Status: <b>{'🟢 Enabled' if cfg.enabled else '🔴 Disabled'}</b>\n"
        f"• Proxy Host: <code>{cfg.proxy_host}</code>\n"
        f"• Proxy Port: <code>{cfg.proxy_port}</code>\n"
        f"• Connection Timeout: <b>{cfg.connection_timeout} seconds</b>\n"
        f"• Max Consecutive Failures: <b>{cfg.max_failed_attempts} attempts</b>\n"
        f"• Restriction Timeout Duration: <b>{cfg.timeout_minutes} minutes</b>\n\n"
        "Tap any parameter below to update its value:"
    )

    if query.message:
        await query.message.edit_text(text, reply_markup=get_admin_free_indo_settings_keyboard(cfg), parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:toggle_enabled")
async def handle_admin_toggle_enabled(query: CallbackQuery) -> None:
    """Toggle enabled status."""
    if not is_admin_check(query.from_user.id):
        await query.answer("Access denied.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        new_state = not cfg.enabled
        await FreeIndoService.update_settings(db, enabled=new_state)

    state_desc = "🟢 Enabled" if new_state else "🔴 Disabled"
    await query.answer(f"Free Indo IGS is now {state_desc}!", show_alert=True)
    await handle_admin_settings_view(query)


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:set_host")
async def handle_set_host_start(query: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_host)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:settings")]
    ])
    if query.message:
        await query.message.edit_text("🌐 Send the new <b>Proxy Hostname</b> (e.g. <code>pr.oxy.labs.io</code>):", reply_markup=cancel_kb, parse_mode="HTML")


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_host)
async def handle_set_host_save(message: Message, state: FSMContext) -> None:
    if not is_admin_check(message.from_user.id):
        return
    new_host = (message.text or "").strip().lower()
    if not new_host:
        await message.answer("Invalid host. Operation cancelled.")
        await state.clear()
        return

    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, proxy_host=new_host)
    await state.clear()
    await message.answer(f"✅ Proxy Host updated to: <code>{new_host}</code>", parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:set_port")
async def handle_set_port_start(query: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_port)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:settings")]
    ])
    if query.message:
        await query.message.edit_text("🔌 Send the new <b>Proxy Port</b> (e.g. <code>7777</code>):", reply_markup=cancel_kb, parse_mode="HTML")


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_port)
async def handle_set_port_save(message: Message, state: FSMContext) -> None:
    if not is_admin_check(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer("Port must be numeric (e.g. 7777). Operation cancelled.")
        await state.clear()
        return

    port_num = int(raw)
    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, proxy_port=port_num)
    await state.clear()
    await message.answer(f"✅ Proxy Port updated to: <code>{port_num}</code>", parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:set_timeout")
async def handle_set_timeout_start(query: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_timeout)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:settings")]
    ])
    if query.message:
        await query.message.edit_text("⏱️ Send the connection timeout in seconds (e.g. <code>15</code>):", reply_markup=cancel_kb, parse_mode="HTML")


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_timeout)
async def handle_set_timeout_save(message: Message, state: FSMContext) -> None:
    if not is_admin_check(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Timeout must be a positive integer. Cancelled.")
        await state.clear()
        return

    sec = int(raw)
    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, connection_timeout=sec)
    await state.clear()
    await message.answer(f"✅ Connection timeout set to: <b>{sec}s</b>", parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:set_max_fails")
async def handle_set_max_fails_start(query: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_max_fails)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:settings")]
    ])
    if query.message:
        await query.message.edit_text("🚫 Send the maximum consecutive failed attempts before timeout (default <code>5</code>):", reply_markup=cancel_kb, parse_mode="HTML")


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_max_fails)
async def handle_set_max_fails_save(message: Message, state: FSMContext) -> None:
    if not is_admin_check(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Invalid value. Cancelled.")
        await state.clear()
        return

    fails = int(raw)
    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, max_failed_attempts=fails)
    await state.clear()
    await message.answer(f"✅ Max consecutive failed attempts set to: <b>{fails}</b>", parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:set_timeout_mins")
async def handle_set_timeout_mins_start(query: CallbackQuery, state: FSMContext) -> None:
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_timeout_mins)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:settings")]
    ])
    if query.message:
        await query.message.edit_text("⏳ Send restriction timeout duration in minutes (default <code>20</code>):", reply_markup=cancel_kb, parse_mode="HTML")


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_timeout_mins)
async def handle_set_timeout_mins_save(message: Message, state: FSMContext) -> None:
    if not is_admin_check(message.from_user.id):
        return
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Invalid duration. Cancelled.")
        await state.clear()
        return

    mins = int(raw)
    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, timeout_minutes=mins)
    await state.clear()
    await message.answer(f"✅ Timeout restriction duration set to: <b>{mins} minutes</b>", parse_mode="HTML")


# =========================================================================
# Tutorial Video Management
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo:tutorial")
async def handle_admin_tutorial_view(query: CallbackQuery) -> None:
    """View tutorial video status."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        has_video = bool(cfg.tutorial_file_id)

    status_str = f"✅ Configured (<code>{cfg.tutorial_file_id}</code>)" if has_video else "⚠️ Not configured"
    text = (
        "🎥 <b>Tutorial Video Management</b>\n\n"
        f"<b>Current Status:</b> {status_str}\n\n"
        "You can upload or forward any tutorial video to this bot to save its Telegram <code>file_id</code>.\n"
        "Users who tap 'Watch Tutorial' will receive this exact video."
    )

    if query.message:
        await query.message.edit_text(text, reply_markup=get_admin_free_indo_tutorial_keyboard(has_video), parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:upload_tutorial")
async def handle_admin_upload_tutorial_start(query: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to send/forward video."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()
    await state.set_state(AdminFreeIndoState.waiting_for_tutorial_video)
    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:tutorial")]
    ])
    if query.message:
        await query.message.edit_text(
            "📤 Please <b>send or forward</b> the tutorial video directly to this chat.\n\n"
            "The bot will automatically extract and store its Telegram <code>file_id</code> without re-uploading.",
            reply_markup=cancel_kb,
            parse_mode="HTML"
        )


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_tutorial_video)
async def handle_admin_save_tutorial_video(message: Message, state: FSMContext) -> None:
    """Extract and save video file_id."""
    if not is_admin_check(message.from_user.id):
        return

    file_id = None
    if message.video:
        file_id = message.video.file_id
    elif message.document and (message.document.mime_type or "").startswith("video/"):
        file_id = message.document.file_id

    if not file_id:
        await message.answer("⚠️ That was not a video. Please send or forward a valid video file:")
        return

    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, tutorial_file_id=file_id)

    await state.clear()
    await message.answer(
        f"✅ <b>Tutorial Video Saved Successfully!</b>\n\nTelegram <code>file_id</code>: <code>{file_id}</code>",
        parse_mode="HTML"
    )


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:remove_tutorial")
async def handle_admin_remove_tutorial(query: CallbackQuery) -> None:
    """Remove configured tutorial video."""
    if not is_admin_check(query.from_user.id):
        return

    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, tutorial_file_id=None)

    await query.answer("Tutorial video removed.", show_alert=True)
    await handle_admin_tutorial_view(query)


# =========================================================================
# Additional Feature: Stored Proxies Retrieval & Copy System
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo:stored_proxies")
async def handle_admin_stored_proxies_menu(query: CallbackQuery) -> None:
    """Display stored proxies dashboard with retrieval buttons."""
    if not is_admin_check(query.from_user.id):
        await query.answer("Access denied.", show_alert=True)
        return
    await query.answer()

    async with AsyncSessionLocal() as db:
        stats = await FreeIndoService.get_proxy_statistics(db)

    text = (
        "📦 <b>Approved Stored Proxies</b>\n\n"
        f"• Total Stored Proxies: <b>{stats['total_stored']}</b>\n"
        f"• Available Proxies: <b>{stats['available_proxies']}</b>\n"
        f"• Retrieved Proxies: <b>{stats['retrieved_proxies']}</b>\n\n"
        "Choose an amount to retrieve approved proxies in format:\n"
        "<code>server:port:username:password</code>\n\n"
        "<i>Note: Retrieval is read-only. Proxies are never deleted.</i>"
    )

    if query.message:
        await query.message.edit_text(text, reply_markup=get_admin_stored_proxies_menu_keyboard(), parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data.startswith("admin_free_indo:get_p:"))
async def handle_admin_retrieve_proxies(query: CallbackQuery, state: FSMContext) -> None:
    """Retrieve specified quantity of approved proxies."""
    if not is_admin_check(query.from_user.id):
        await query.answer("Access denied.", show_alert=True)
        return

    parts = query.data.split(":")
    amount_str = parts[2] if len(parts) > 2 else "1"

    if amount_str == "custom":
        await query.answer()
        await state.set_state(AdminFreeIndoState.waiting_for_custom_proxy_amount)
        cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Cancel", callback_data="admin_free_indo:stored_proxies")]
        ])
        if query.message:
            await query.message.edit_text("🔢 Enter the required number of proxies to retrieve:", reply_markup=cancel_kb)
        return

    await query.answer()
    try:
        qty = int(amount_str)
    except ValueError:
        qty = 1

    await execute_proxy_retrieval(query, qty)


@admin_free_indo_router.message(AdminFreeIndoState.waiting_for_custom_proxy_amount)
async def handle_custom_amount_save(message: Message, state: FSMContext) -> None:
    """Process custom quantity message from admin."""
    if not is_admin_check(message.from_user.id):
        return

    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        await message.answer("Please enter a valid positive number.")
        return

    qty = min(500, int(raw))
    await state.clear()

    async with AsyncSessionLocal() as db:
        proxies = await FreeIndoService.get_stored_proxies(db, limit=qty)

    if not proxies:
        await message.answer("⚠️ No approved stored proxies available in database.")
        return

    all_text = "\n".join(p["formatted"] for p in proxies)

    response_lines = [
        f"📦 <b>Retrieved Approved Proxies ({len(proxies)}):</b>\n",
        "```text",
        all_text,
        "```"
    ]

    kb = get_admin_retrieved_proxies_keyboard(proxies, all_text)
    await message.answer("\n".join(response_lines), reply_markup=kb, parse_mode="HTML")


async def execute_proxy_retrieval(query: CallbackQuery, qty: int) -> None:
    """Execute read-only proxy retrieval and display formatted cards with copy buttons."""
    async with AsyncSessionLocal() as db:
        proxies = await FreeIndoService.get_stored_proxies(db, limit=qty)

    if not proxies:
        if query.message:
            back_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Back", callback_data="admin_free_indo:stored_proxies")]
            ])
            await query.message.edit_text("⚠️ <b>No approved stored proxies available.</b>", reply_markup=back_kb, parse_mode="HTML")
        return

    all_text = "\n".join(p["formatted"] for p in proxies)

    header_text = (
        f"📦 <b>Retrieved Approved Proxies ({len(proxies)}):</b>\n\n"
        f"<code>{html.escape(all_text)}</code>"
    )

    kb = get_admin_retrieved_proxies_keyboard(proxies, all_text)

    if query.message:
        try:
            await query.message.edit_text(header_text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await query.message.answer(header_text, reply_markup=kb, parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data.startswith("admin_free_indo:copy_fallback:"))
async def handle_copy_fallback(query: CallbackQuery) -> None:
    """Fallback handler that sends copyable text as an individual message."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()

    parts = query.data.split(":")
    target = parts[2] if len(parts) > 2 else "all"

    async with AsyncSessionLocal() as db:
        if target == "all":
            proxies = await FreeIndoService.get_stored_proxies(db, limit=50)
            txt = "\n".join(p["formatted"] for p in proxies)
        else:
            try:
                pid = int(target)
                proxies = await FreeIndoService.get_stored_proxies(db, limit=100)
                matched = [p for p in proxies if p["id"] == pid]
                txt = matched[0]["formatted"] if matched else ""
            except Exception:
                txt = ""

    if txt and query.message:
        await query.message.answer(f"<code>{html.escape(txt)}</code>", parse_mode="HTML")


# =========================================================================
# Rewards & Pending Queue Management
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo:rewards")
async def handle_admin_rewards_view(query: CallbackQuery) -> None:
    """View rewards and pending queue."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()

    async with AsyncSessionLocal() as db:
        stats = await FreeIndoService.get_proxy_statistics(db)

    text = (
        "🏆 <b>Reward Management & Pending Queue</b>\n\n"
        f"• Total Rewards Delivered: <b>{stats['total_rewards_delivered']}</b>\n"
        f"• Pending Rewards Queue: <b>{stats['pending_rewards']}</b>\n\n"
        "If account stock was empty when a user earned a reward, it is queued as PENDING.\n"
        "Tap below to attempt automatic delivery of all pending rewards using available stock:"
    )

    if query.message:
        await query.message.edit_text(text, reply_markup=get_admin_pending_rewards_keyboard(), parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data == "admin_free_indo:deliver_pending")
async def handle_admin_deliver_pending(query: CallbackQuery) -> None:
    """Trigger manual delivery of all pending rewards."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer("Processing pending rewards...", show_alert=False)

    delivered = await FreeIndoService.deliver_pending_rewards(bot=query.bot)

    if delivered > 0:
        await query.answer(f"🎉 Successfully delivered {delivered} pending reward(s)!", show_alert=True)
    else:
        await query.answer("No pending rewards could be delivered (check available stock).", show_alert=True)

    await handle_admin_rewards_view(query)


# =========================================================================
# User Timeout & Anti-Spam Management
# =========================================================================

@admin_free_indo_router.callback_query(F.data == "admin_free_indo:timeouts")
async def handle_admin_timeouts_view(query: CallbackQuery) -> None:
    """View and unban users currently under timeout."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()

    async with AsyncSessionLocal() as db:
        users = await FreeIndoService.get_active_timeout_users(db)

    if not users:
        text = (
            "👥 <b>User Timeout Management</b>\n\n"
            "✅ <i>There are currently no restricted users under active timeout.</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Free Indo Admin", callback_data="admin_free_indo", style="danger")]
        ])
    else:
        lines = [f"👥 <b>Restricted Users Under Timeout ({len(users)}):</b>\n"]
        for u in users:
            lines.append(f"• User ID <code>{u['telegram_user_id']}</code>: {u['remaining_minutes']}m left (Failures: {u['consecutive_failures']})")
        lines.append("\nTap a button below to remove restriction:")
        text = "\n".join(lines)
        kb = get_admin_timeout_users_keyboard(users)

    if query.message:
        await query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")


@admin_free_indo_router.callback_query(F.data.startswith("admin_free_indo:unban:"))
async def handle_admin_unban_user(query: CallbackQuery) -> None:
    """Unban / clear timeout for specific user."""
    if not is_admin_check(query.from_user.id):
        return

    uid_str = query.data.split(":")[2]
    try:
        target_uid = int(uid_str)
    except ValueError:
        return

    async with AsyncSessionLocal() as db:
        await FreeIndoService.clear_timeout(db, target_uid)

    await query.answer(f"✅ Timeout removed for user {target_uid}!", show_alert=True)
    await handle_admin_timeouts_view(query)


# =========================================================================
# Submission Logs Inspection
# =========================================================================

@admin_free_indo_router.callback_query(F.data.startswith("admin_free_indo:submissions:"))
async def handle_admin_submissions_log(query: CallbackQuery) -> None:
    """Inspect submission history with status filtering and pagination."""
    if not is_admin_check(query.from_user.id):
        return
    await query.answer()

    parts = query.data.split(":")
    status_filter = parts[2] if len(parts) > 2 else "ALL"
    try:
        offset = int(parts[3]) if len(parts) > 3 else 0
    except ValueError:
        offset = 0

    limit = 8
    async with AsyncSessionLocal() as db:
        records, total = await FreeIndoService.query_submissions(
            db=db,
            status_filter=status_filter,
            limit=limit,
            offset=offset
        )

    lines = [f"📋 <b>Submission Logs (Filter: {status_filter} | Total: {total})</b>\n"]
    if not records:
        lines.append("<i>No submissions found for this filter.</i>")
    else:
        tz = ZoneInfo("Asia/Kolkata")
        for sub in records:
            created_ist = sub.created_at.astimezone(tz) if sub.created_at.tzinfo else sub.created_at
            date_str = created_ist.strftime("%d %b %H:%M:%S")
            status_icon = "🟢" if sub.status == "APPROVED" else ("🔴" if sub.status == "REJECTED" else "⚠️")
            resp_str = f" ({sub.response_time_ms}ms)" if sub.response_time_ms else ""
            lines.append(
                f"{status_icon} <b>#{sub.id}</b> | <code>{sub.telegram_user_id}</code>\n"
                f"   User: <code>{html.escape(sub.username)}</code> | {sub.status}{resp_str}\n"
                f"   Time: <i>{date_str} IST</i>"
            )

    # Build pagination and filter buttons
    nav_buttons = []
    if offset > 0:
        prev_off = max(0, offset - limit)
        nav_buttons.append(InlineKeyboardButton(text="« Prev", callback_data=f"admin_free_indo:submissions:{status_filter}:{prev_off}"))
    if offset + limit < total:
        next_off = offset + limit
        nav_buttons.append(InlineKeyboardButton(text="Next »", callback_data=f"admin_free_indo:submissions:{status_filter}:{next_off}"))

    filter_buttons = [
        InlineKeyboardButton(text="All", callback_data=f"admin_free_indo:submissions:ALL:0"),
        InlineKeyboardButton(text="Approved", callback_data=f"admin_free_indo:submissions:APPROVED:0"),
        InlineKeyboardButton(text="Rejected", callback_data=f"admin_free_indo:submissions:REJECTED:0"),
    ]

    kb_rows = []
    if nav_buttons:
        kb_rows.append(nav_buttons)
    kb_rows.append(filter_buttons)
    kb_rows.append([InlineKeyboardButton(text="« Back to Free Indo Admin", callback_data="admin_free_indo", style="danger")])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)

    if query.message:
        await query.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")
