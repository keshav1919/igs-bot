"""
Free Indo IGS Customer Handlers for aiogram 3 Telegram Bot.
Handles /start integration, Free Indo IGS menu, instructions, tutorial video delivery,
progress tracking, and intelligent single/two-message proxy submissions with atomic verification.
"""
import html
import logging
import re
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
from indo_selling_bot.services.proxy_parser import ProxyCredentialParser
from indo_selling_bot.bot.keyboards import (
    get_free_indo_main_keyboard,
    get_free_indo_back_keyboard,
    get_free_indo_cancel_keyboard,
)

logger = logging.getLogger("free_indo_handlers")

free_indo_router = Router()


class FreeIndoSubmitState(StatesGroup):
    waiting_for_credentials = State()


# =========================================================================
# Main Menu & Navigation Handlers
# =========================================================================

@free_indo_router.callback_query(F.data == "free_indo_igs_menu")
@free_indo_router.message(Command("freeindo"))
async def handle_free_indo_menu(event: Message | CallbackQuery, state: FSMContext | None = None) -> None:
    """Open Free Indo IGS main menu section."""
    if state:
        await state.clear()

    if isinstance(event, CallbackQuery):
        await event.answer()

    menu_text = (
        "🎁 <b>FREE INDO IGS</b>\n\n"
        "Earn free Instagram accounts by submitting valid mobile proxy credentials.\n\n"
        "<b>Reward:</b>\n\n"
        "2 Successfully Verified Unique Proxies = 1 FREE IGS Account."
    )

    kb = get_free_indo_main_keyboard()

    if isinstance(event, CallbackQuery) and event.message:
        try:
            await event.message.edit_text(menu_text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            await event.message.answer(menu_text, reply_markup=kb, parse_mode="HTML")
    elif isinstance(event, Message):
        await event.answer(menu_text, reply_markup=kb, parse_mode="HTML")


@free_indo_router.callback_query(F.data == "free_indo:how_it_works")
async def handle_how_it_works(query: CallbackQuery) -> None:
    """Display improved English instructions with dynamic server and port."""
    await query.answer()

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        host = cfg.proxy_host
        port = cfg.proxy_port

    instructions = (
        "🎁 <b>Earn FREE Instagram Accounts!</b>\n\n"
        "Get 1 FREE IGS account for every 2 successfully verified mobile proxies.\n\n"
        "📋 <b>How to Participate:</b>\n\n"
        "1️⃣ Create an account with an authorized mobile proxy provider.\n\n"
        "2️⃣ Open your provider dashboard and add a Mobile Proxy product.\n\n"
        "3️⃣ Configure your proxy product and obtain your proxy username and password.\n\n"
        "4️⃣ Submit your proxy credentials to this bot for automatic verification.\n\n"
        "5️⃣ Once 2 unique proxies are successfully verified, you will receive 1 FREE IGS account!\n\n"
        "<b>📌 Required Proxy Details</b>\n\n"
        f"Proxy Server: <code>{host}</code>\n"
        f"Port: <code>{port}</code>\n"
        "Proxy Type: HTTP\n\n"
        "<b>Send your credentials in either of these formats:</b>\n\n"
        "Format 1:\n"
        "<code>username:password</code>\n\n"
        "Format 2:\n"
        "<code>username</code>\n"
        "<code>password</code>\n\n"
        "Example:\n"
        "<code>mobile_97d8c330:YOUR_PASSWORD</code>\n\n"
        "Or:\n"
        "Username: <code>mobile_97d8c330</code>\n"
        "Password: <code>YOUR_PASSWORD</code>\n\n"
        "⚠️ Only submit credentials you own or are authorized to use and share."
    )

    if query.message:
        try:
            await query.message.edit_text(instructions, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")
        except Exception:
            await query.message.answer(instructions, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")


@free_indo_router.callback_query(F.data == "free_indo:tutorial")
async def handle_watch_tutorial(query: CallbackQuery) -> None:
    """Send configured tutorial video or unavailable alert."""
    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        file_id = cfg.tutorial_file_id

    if not file_id:
        await query.answer("⚠️ Tutorial video is currently unavailable. Please check again later.", show_alert=True)
        return

    await query.answer()
    if query.message:
        try:
            await query.message.answer_video(
                video=file_id,
                caption="🎥 <b>Free Indo IGS Tutorial Video</b>\n\nFollow the instructions above to configure and obtain your mobile proxy credentials.",
                parse_mode="HTML",
                reply_markup=get_free_indo_back_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to send tutorial video file_id={file_id}: {e}")
            await query.message.answer(
                "⚠️ Tutorial video is currently unavailable. Please check again later.",
                reply_markup=get_free_indo_back_keyboard()
            )


@free_indo_router.callback_query(F.data == "free_indo:progress")
async def handle_my_progress(query: CallbackQuery) -> None:
    """Display user's approved proxy count, remaining count, and accounts received."""
    await query.answer()
    user_id = query.from_user.id if query.from_user else 0

    async with AsyncSessionLocal() as db:
        prog = await FreeIndoService.get_user_progress(db, user_id)

    progress_text = (
        "🎁 <b>FREE INDO IGS PROGRESS</b>\n\n"
        f"Approved Proxies: {prog['current_cycle_approved']}/2\n"
        f"Remaining Proxies: {prog['remaining_for_reward']}\n"
        f"Accounts Received: {prog['rewards_delivered']}\n"
        f"Pending Rewards: {prog['pending_rewards']}"
    )

    if prog["pending_rewards"] > 0:
        progress_text += "\n\n<i>⚠️ You have pending rewards! They will be delivered automatically as soon as account stock is replenished.</i>"

    if query.message:
        try:
            await query.message.edit_text(progress_text, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")
        except Exception:
            await query.message.answer(progress_text, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")


# =========================================================================
# Proxy Submission Handlers
# =========================================================================

@free_indo_router.callback_query(F.data == "free_indo:submit")
async def handle_submit_proxy_start(query: CallbackQuery, state: FSMContext) -> None:
    """Check feature availability, rate limit, and prompt for credentials."""
    user = query.from_user
    user_id = user.id if user else 0
    is_admin = settings.is_admin(user_id)

    async with AsyncSessionLocal() as db:
        cfg = await FreeIndoService.get_settings(db)
        if not cfg.enabled and not is_admin:
            await query.answer("⚠️ Free Indo IGS is currently paused for maintenance. Please check back later.", show_alert=True)
            return

        is_restr, rem_mins, _ = await FreeIndoService.check_user_timeout(db, user_id, is_admin)
        if is_restr:
            restr_msg = (
                "🚫 <b>Submission Temporarily Restricted</b>\n\n"
                "You have submitted too many invalid proxy credentials.\n\n"
                f"Your submission access has been temporarily restricted for {rem_mins} minutes.\n\n"
                "Please try again after the timeout expires.\n\n"
                "Your previously approved proxies and reward progress are safe."
            )
            await query.answer("🚫 Submission Temporarily Restricted", show_alert=True)
            if query.message:
                try:
                    await query.message.edit_text(restr_msg, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")
                except Exception:
                    await query.message.answer(restr_msg, reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")
            return

    await query.answer()
    await state.set_state(FreeIndoSubmitState.waiting_for_credentials)
    await state.set_data({})  # Reset any pending partial credentials

    prompt_text = (
        "🚀 <b>Submit Mobile Proxy Credentials</b>\n\n"
        "Please send your proxy credentials in either format:\n\n"
        "<b>Format 1:</b>\n"
        "<code>username:password</code>\n\n"
        "<b>Format 2:</b>\n"
        "<code>username</code>\n"
        "<code>password</code>\n\n"
        "<b>Example:</b>\n"
        "<code>mobile_97d8c330:YOUR_PASSWORD</code>\n\n"
        "<i>Tip: You can also send username and password in two separate messages.</i>"
    )

    if query.message:
        try:
            await query.message.edit_text(prompt_text, reply_markup=get_free_indo_cancel_keyboard(), parse_mode="HTML")
        except Exception:
            await query.message.answer(prompt_text, reply_markup=get_free_indo_cancel_keyboard(), parse_mode="HTML")


@free_indo_router.message(FreeIndoSubmitState.waiting_for_credentials)
async def handle_proxy_submission_message(message: Message, state: FSMContext) -> None:
    """
    Process incoming proxy credentials text.
    Handles single-message and two-message flows with full error and timeout tracking.
    """
    user = message.from_user
    user_id = user.id if user else 0
    is_admin = settings.is_admin(user_id)
    raw_text = (message.text or "").strip()

    if not raw_text:
        await message.answer("⚠️ Empty input. Please send your proxy credentials:", reply_markup=get_free_indo_cancel_keyboard())
        return

    # Check timeout before processing
    async with AsyncSessionLocal() as db:
        is_restr, rem_mins, _ = await FreeIndoService.check_user_timeout(db, user_id, is_admin)
        if is_restr:
            await state.clear()
            await message.answer(
                "🚫 <b>Submission Temporarily Restricted</b>\n\n"
                "You have submitted too many invalid proxy credentials.\n\n"
                f"Your submission access has been temporarily restricted for {rem_mins} minutes.\n\n"
                "Please try again after the timeout expires.\n\n"
                "Your previously approved proxies and reward progress are safe.",
                reply_markup=get_free_indo_back_keyboard(),
                parse_mode="HTML"
            )
            return

    # 1. Try single-message parsing first
    parsed_user, parsed_pass, _ = ProxyCredentialParser.parse(raw_text)

    # 2. If not both present, check two-message state
    data = await state.get_data()
    pending_user = data.get("pending_user")
    pending_pass = data.get("pending_pass")

    if not parsed_user or not parsed_pass:
        if pending_user and not pending_pass:
            # We already have username; current message must be password
            parsed_user = pending_user
            # Extract password label if present
            m_pass = re.match(r"(?i)^(?:password|pass|pwd)\s*[:=\-]\s*(.+)$", raw_text)
            parsed_pass = m_pass.group(1).strip() if m_pass else raw_text
        elif pending_pass and not pending_user:
            # We already have password; current message must be username
            parsed_pass = pending_pass
            m_user = re.match(r"(?i)^(?:username|user\s*name|user|usr)\s*[:=\-]\s*(.+)$", raw_text)
            parsed_user = m_user.group(1).strip() if m_user else raw_text
        else:
            # Check if this single message provides ONLY username or ONLY password
            field_type, field_val = ProxyCredentialParser.identify_single_field(raw_text)
            if field_type == "username" and field_val:
                await state.update_data(pending_user=field_val)
                await message.answer(
                    f"✅ Username received: <code>{html.escape(field_val)}</code>\n\n"
                    "Now please send your <b>Password</b>:",
                    reply_markup=get_free_indo_cancel_keyboard(),
                    parse_mode="HTML"
                )
                return
            elif field_type == "password" and field_val:
                await state.update_data(pending_pass=field_val)
                await message.answer(
                    "✅ Password received.\n\n"
                    "Now please send your <b>Username</b>:",
                    reply_markup=get_free_indo_cancel_keyboard(),
                    parse_mode="HTML"
                )
                return
            else:
                # Completely unrecognized format -> Counts as failed attempt
                async with AsyncSessionLocal() as db:
                    cfg = await FreeIndoService.get_settings(db)
                    timed_out, mins, total_f = await FreeIndoService.record_failure(
                        db=db,
                        telegram_user_id=user_id,
                        is_admin=is_admin,
                        max_fails=cfg.max_failed_attempts,
                        timeout_minutes=cfg.timeout_minutes
                    )

                if timed_out:
                    await state.clear()
                    await message.answer(
                        "🚫 <b>Submission Temporarily Restricted</b>\n\n"
                        "You have submitted too many invalid proxy credentials.\n\n"
                        f"Your submission access has been temporarily restricted for {mins} minutes.\n\n"
                        "Please try again after the timeout expires.\n\n"
                        "Your previously approved proxies and reward progress are safe.",
                        reply_markup=get_free_indo_back_keyboard(),
                        parse_mode="HTML"
                    )
                    return

                attempt_str = f" (Attempt {total_f}/{cfg.max_failed_attempts})" if not is_admin else ""
                await message.answer(
                    f"⚠️ <b>Could not identify valid proxy credentials.</b>{attempt_str}\n\n"
                    "Please send your credentials in either format:\n"
                    "<code>username:password</code>\n\n"
                    "Or:\n"
                    "Username: <code>your_username</code>\n"
                    "Password: <code>your_password</code>",
                    reply_markup=get_free_indo_cancel_keyboard(),
                    parse_mode="HTML"
                )
                return

    # Clear FSM state now that we have complete credentials
    await state.clear()

    # Send in-flight status message
    status_msg = await message.answer("⏳ <i>Verifying proxy credentials... Please wait.</i>", parse_mode="HTML")

    # Execute verification pipeline
    res = await FreeIndoService.submit_and_verify_proxy(
        telegram_user_id=user_id,
        username=parsed_user,
        password=parsed_pass,
        is_admin=is_admin
    )

    status = res.get("status")

    if status == "DUPLICATE":
        await status_msg.edit_text(
            "⚠️ This proxy has already been verified and cannot be used again for another reward.",
            reply_markup=get_free_indo_back_keyboard()
        )
        return

    if status == "RESTRICTED":
        await status_msg.edit_text(res.get("message", "🚫 Submission Restricted"), reply_markup=get_free_indo_back_keyboard(), parse_mode="HTML")
        return

    if status == "DISABLED":
        await status_msg.edit_text(res.get("message", "⚠️ Feature Paused"), reply_markup=get_free_indo_back_keyboard())
        return

    if status == "APPROVED":
        is_milestone = res.get("is_milestone", False)
        reward_status = res.get("reward_status")
        account = res.get("account")

        if not is_milestone:
            success_text = (
                "🟢 <b>APPROVED</b>\n\n"
                "Congratulations! Your first proxy has been successfully verified.\n\n"
                "Submit one more unique proxy to receive your FREE IGS account."
            )
            await status_msg.edit_text(success_text, reply_markup=get_free_indo_main_keyboard(), parse_mode="HTML")
            return
        else:
            # Reward earned!
            if reward_status == "DELIVERED" and account:
                acc_email = account.get("email", "")
                acc_pass = account.get("password", "")
                acc_user = account.get("username", "")
                acc_cookie = account.get("cookie", "")
                acc_date = account.get("timestamp_str") or ""

                delivery_card = [
                    "🎉 <b>Congratulations!</b>\n",
                    "You have successfully completed the Free Indo IGS offer.\n",
                    "Your FREE Instagram account has been delivered!\n",
                    "Thank you for participating. ❤️\n",
                    "━━━━━━━━━━━━━━━━━━",
                    f"📧 Email : <code>{html.escape(acc_email)}</code>",
                ]
                if acc_user:
                    delivery_card.append(f"🫟 UserName : <code>{html.escape(acc_user)}</code>")
                delivery_card.append(f"🔑 Password : <code>{html.escape(acc_pass)}</code>")
                if acc_cookie:
                    delivery_card.append(f"🍪 Cookie : <code>{html.escape(acc_cookie)}</code>")
                if acc_date:
                    delivery_card.append(f"🕒 Created at : <code>{html.escape(acc_date)}</code>")
                delivery_card.append("━━━━━━━━━━━━━━━━━━")

                delivery_text = "\n".join(delivery_card)
                await status_msg.edit_text(delivery_text, reply_markup=get_free_indo_main_keyboard(), parse_mode="HTML")
                return
            else:
                # Stock is currently empty; reward saved as PENDING
                pending_text = (
                    "🎉 <b>Congratulations!</b>\n\n"
                    "You have successfully completed the Free Indo IGS offer.\n\n"
                    "⚠️ Account stock is currently being replenished. Your reward has been saved as <b>PENDING</b> "
                    "and will be delivered to you automatically as soon as stock is added!\n\n"
                    "Thank you for participating. ❤️"
                )
                await status_msg.edit_text(pending_text, reply_markup=get_free_indo_main_keyboard(), parse_mode="HTML")
                return

    elif status == "REJECTED":
        if res.get("timed_out"):
            mins = res.get("timeout_minutes", 20)
            await status_msg.edit_text(
                "🚫 <b>Submission Temporarily Restricted</b>\n\n"
                "You have submitted too many invalid proxy credentials.\n\n"
                f"Your submission access has been temporarily restricted for {mins} minutes.\n\n"
                "Please try again after the timeout expires.\n\n"
                "Your previously approved proxies and reward progress are safe.",
                reply_markup=get_free_indo_back_keyboard(),
                parse_mode="HTML"
            )
        else:
            attempt_str = f"\n\n(Attempt {res.get('consecutive_failures')}/{res.get('max_fails')})" if not is_admin else ""
            await status_msg.edit_text(
                "🔴 <b>REJECTED</b>\n\n"
                "The proxy returned an authentication failure or invalid credentials.\n\n"
                f"Please check your username and password and try again.{attempt_str}",
                reply_markup=get_free_indo_main_keyboard(),
                parse_mode="HTML"
            )
        return

    elif status == "TIMEOUT":
        await status_msg.edit_text(
            "🟡 <b>TIMEOUT</b>\n\n"
            "The proxy server did not respond within the connection timeout.\n"
            "This could be due to proxy latency. Please try again later.",
            reply_markup=get_free_indo_main_keyboard(),
            parse_mode="HTML"
        )
        return

    else:  # TEMPORARY_ERROR
        await status_msg.edit_text(
            "⚠️ <b>TEMPORARY ERROR</b>\n\n"
            "A network or test endpoint issue prevented reliable verification.\n"
            "Please try again in a few moments.",
            reply_markup=get_free_indo_main_keyboard(),
            parse_mode="HTML"
        )
        return
