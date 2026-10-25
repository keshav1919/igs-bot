"""
Add Fund & UPI Payment Workflow.
Implements:
- Part 9: UPI configuration (paytm.s1x87m2@pty), server-side ₹30 minimum validation
- Part 10: Dynamic UPI QR code generation with amount and payment request ID
- Part 11: Customer payment screenshot upload and association with pending requests
- Part 12: Admin payment notification, authorized approval, and rejection
- Part 13: Customer transaction and deposit history
"""
import io
import html
import logging
import urllib.parse
from datetime import datetime, timezone
import qrcode

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy import select, update
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.wallet import DepositRequest
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.payment_setting_service import PaymentSettingService
from indo_selling_bot.services.truex_service import TrueXGatewayService, polling_manager
from indo_selling_bot.bot.keyboards import (
    get_add_fund_keyboard,
    get_deposit_request_keyboard,
    get_truex_payment_keyboard,
    get_admin_deposit_review_keyboard,
    get_my_wallet_keyboard,
    get_main_reply_keyboard,
)
from indo_selling_bot.config import settings

logger = logging.getLogger("add_fund")

add_fund_router = Router()

class AddFundState(StatesGroup):
    waiting_for_amount = State()
    waiting_for_screenshot = State()


def _is_admin(user_id: int | None = None, username: str | None = None) -> bool:
    return settings.is_admin(user_id, username)


def generate_upi_qr_bytes(upi_id: str, payee_name: str, amount: float, ref_id: str) -> bytes:
    """Generate dynamic PNG QR code containing exact UPI payment URI."""
    pn_encoded = urllib.parse.quote(payee_name)
    tn_encoded = urllib.parse.quote(f"Topup {ref_id}")
    upi_uri = f"upi://pay?pa={upi_id}&pn={pn_encoded}&am={amount:.2f}&cu=INR&tr={ref_id}&tn={tn_encoded}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(upi_uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ==========================================
# 1. Add Fund Main Information Screen
# ==========================================
async def _send_add_fund_info(target: Message | CallbackQuery, user_id: int, username: str | None = None) -> None:
    """Helper to display business payment instructions and minimum requirement."""
    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)

    is_admin = _is_admin(user_id, username)
    min_dep = float(settings.MIN_DEPOSIT_AMOUNT)
    min_str = "No limit (Admin)" if is_admin else f"₹{int(min_dep)}"

    text = (
        "💰 <b>Add Funds to Your Wallet</b>\n\n\n"
        f"⚠️ <b>Minimum Deposit:</b> {min_str}\n"
        f"💳 <b>Current Wallet Balance:</b> ₹{balance:,.2f}\n\n"
        "📋 <b>How to Pay:</b>\n"
        "1. Click <b>➕ Add Fund</b> below.\n"
        "2. Enter deposit amount.\n"
        "3. Scan the dynamic QR code with any UPI app.\n"
        "4. Pay the exact decimal amount.\n"
        "5. <b>Automatic Verification:</b> Funds credit automatically!\n\n"
        "Click ➕ <b>Add Fund</b> to generate your QR:"
    )

    keyboard = get_add_fund_keyboard()

    if isinstance(target, CallbackQuery):
        try:
            await target.answer()
        except Exception:
            pass
        if target.message:
            try:
                await target.message.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                try:
                    await target.message.answer(text, parse_mode="HTML", reply_markup=keyboard)
                except Exception:
                    pass
    else:
        await target.answer(text, parse_mode="HTML", reply_markup=keyboard)


@add_fund_router.message(F.text == "💰 Add Fund")
@add_fund_router.message(Command("addfund"))
@add_fund_router.message(Command("fund"))
async def handle_add_fund_menu(message: Message, state: FSMContext) -> None:
    """Entry point for customer adding funds."""
    await state.clear()
    user = message.from_user
    user_id = user.id if user else 0
    username = user.username if user else None
    await _send_add_fund_info(message, user_id, username)


@add_fund_router.callback_query(F.data.in_({"goto_add_fund", "menu_add_fund", "add_fund", "funds_help"}))
async def handle_goto_add_fund_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Redirect to Add Fund from insufficient balance button."""
    await state.clear()
    user = query.from_user
    user_id = user.id if user else 0
    username = user.username if user else None
    await _send_add_fund_info(query, user_id, username)


# ==========================================
# 2. Enter Deposit Amount & Generate Dynamic QR
# ==========================================
@add_fund_router.callback_query(F.data == "fund_enter_amount")
async def handle_fund_enter_amount_click(query: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type deposit amount."""
    await query.answer()
    await state.set_state(AddFundState.waiting_for_amount)
    user = query.from_user
    user_id = user.id if user else 0
    username = user.username if user else None
    is_admin = _is_admin(user_id, username)
    min_dep = float(settings.MIN_DEPOSIT_AMOUNT)
    min_line = "⚠️ <b>Minimum deposit: None (Admin: Any amount >= ₹1)</b>" if is_admin else f"⚠️ <b>Minimum deposit: ₹{int(min_dep)}</b>"
    prompt_text = (
        "💵 <b>Enter Deposit Amount</b>\n\n"
        f"Please reply with the amount you wish to deposit in ₹ (e.g., <code>1</code>, <code>10</code>, <code>50</code>, <code>100</code>).\n\n"
        f"{min_line}\n"
        "<i>Send /cancel to abort at any time.</i>"
    )
    if query.message:
        await query.message.answer(prompt_text, parse_mode="HTML")


@add_fund_router.message(AddFundState.waiting_for_amount, Command("cancel"))
@add_fund_router.message(AddFundState.waiting_for_screenshot, Command("cancel"))
async def handle_cancel_add_fund(message: Message, state: FSMContext) -> None:
    """Cancel active deposit conversation."""
    await state.clear()
    await message.answer("❌ Deposit request cancelled. Returning to main menu.")


@add_fund_router.message(AddFundState.waiting_for_amount)
async def process_deposit_amount(message: Message, state: FSMContext) -> None:
    """
    Validate numeric deposit amount server-side.
    Admins can deposit any amount (no ₹30 limit).
    Generates dynamic TrueXGateway QR code with unique collision-free decimal amount.
    Auto-detects payments in real time and updates message in-place upon verification.
    """
    raw_text = (message.text or "").strip().replace(",", "").replace("₹", "").replace("INR", "").replace("Rp", "").strip()
    user = message.from_user
    if not user:
        await message.answer("User context missing.")
        return

    is_admin = _is_admin(user.id, user.username)
    min_dep = 1.0 if is_admin else float(settings.MIN_DEPOSIT_AMOUNT)

    try:
        base_amount = float(raw_text)
        if base_amount <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        if is_admin:
            await message.answer("⚠️ Please enter a valid deposit amount greater than ₹0.")
        else:
            await message.answer(f"⚠️ Minimum Add Fund Amount is ₹{int(min_dep)}. Please enter a valid number.")
        return

    # Strictly reject below minimum for non-admin customers
    if not is_admin and base_amount < min_dep:
        await message.answer(f"⚠️ Minimum Add Fund Amount is ₹{int(min_dep)}. Please enter ₹{int(min_dep)} or more.")
        return

    # Gateway minimum is ₹1.00 for admin
    if is_admin and base_amount < 1.0:
        await message.answer("⚠️ Minimum amount supported by UPI payment gateway is ₹1.00. Please enter ₹1 or more.")
        return

    # Reject excessive deposit
    if base_amount > float(settings.MAX_DEPOSIT_AMOUNT):
        await message.answer(f"⚠️ Maximum single deposit amount is ₹{settings.MAX_DEPOSIT_AMOUNT:,.2f}. Please enter a smaller amount.")
        return

    status_msg = await message.answer("⏳ Generating dynamic UPI payment QR, please wait...")

    async with AsyncSessionLocal() as db:
        unique_amount = await TrueXGatewayService.calculate_unique_amount(db, base_amount)
        order_ref = await TrueXGatewayService.generate_order_ref(db)

    try:
        order_data = await TrueXGatewayService.create_order(
            amount=unique_amount,
            order_ref=order_ref,
            description=f"Indo Topup {order_ref}"
        )
    except Exception as err:
        logger.error(f"TrueXGateway create-order failed: {err}")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer(f"❌ Failed to generate payment order: {err}\nPlease try again.")
        return

    txg_order_id = order_data.get("orderId") or ""
    qr_url = order_data.get("qrUrl")
    upi_intent = order_data.get("upiIntent") or order_data.get("appUpiIntent")
    checkout_url = order_data.get("checkoutUrl")

    try:
        qr_bytes = await TrueXGatewayService.fetch_qr_image_bytes(
            txg_order_id=txg_order_id,
            qr_url_fallback=qr_url,
            upi_intent=upi_intent
        )
    except Exception as qr_err:
        logger.error(f"Failed to fetch QR image bytes: {qr_err}")
        try:
            await status_msg.delete()
        except Exception:
            pass
        await message.answer("❌ Could not retrieve QR code image from gateway. Please try again.")
        return

    # Create DepositRequest in DB
    async with AsyncSessionLocal() as db:
        deposit = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=user.id,
            amount=unique_amount,
            reference=order_ref,
            txg_order_id=txg_order_id,
            currency="₹",
            username=user.username,
            first_name=user.first_name,
            initial_status="PENDING_PAYMENT"
        )
        await db.commit()

    qr_file = BufferedInputFile(qr_bytes, filename=f"qr_{order_ref}.png")

    # Display amount without decimal in caption (e.g. ₹40), while actual QR contains unique decimal (e.g. 40.02)
    display_amount = int(base_amount) if float(base_amount).is_integer() else f"{base_amount:.2f}"

    caption = (
        f"💰 <b>Amount to Pay:</b> <code>₹{display_amount}</code>\n"
        f"🆔 <b>Order ID:</b> <code>{order_ref}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. Scan the QR code with any UPI app (Paytm, PhonePe, GPay, BHIM).\n"
        "2. Complete the transfer (amount is auto-filled from QR).\n"
        "3. Funds are <b>automatically detected & credited</b> within seconds!\n\n"
        "⏳ <i>Waiting for live bank confirmation...</i>"
    )

    kb = get_truex_payment_keyboard(txg_order_id, app_url=upi_intent or checkout_url)
    try:
        await status_msg.delete()
    except Exception:
        pass

    qr_msg = await message.answer_photo(photo=qr_file, caption=caption, parse_mode="HTML", reply_markup=kb)

    # Save message_id and chat_id in database for in-place success editing
    async with AsyncSessionLocal() as db:
        stmt = (
            update(DepositRequest)
            .where(DepositRequest.reference == order_ref)
            .values(message_id=qr_msg.message_id, chat_id=qr_msg.chat.id)
        )
        await db.execute(stmt)
        await db.commit()

    await state.clear()

    # Launch background polling task for auto-detection
    if message.bot:
        await polling_manager.start_polling(
            bot=message.bot,
            txg_order_id=txg_order_id,
            order_ref=order_ref
        )


@add_fund_router.callback_query(F.data.startswith("txg_chk:"))
async def handle_txg_check_payment(query: CallbackQuery) -> None:
    """Manual status check button clicked by customer on QR screen."""
    txg_order_id = query.data.split(":")[1]
    async with AsyncSessionLocal() as db:
        stmt = select(DepositRequest).where(DepositRequest.txg_order_id == txg_order_id)
        dep = (await db.execute(stmt)).scalar_one_or_none()

    if not dep:
        await query.answer("Payment order not found.", show_alert=True)
        return

    if dep.status in ("APPROVED", "SUCCESS"):
        await query.answer("✅ Payment already verified and credited to your wallet!", show_alert=True)
        return

    try:
        data = await TrueXGatewayService.check_payment_status(txg_order_id)
        status = (data.get("status") or "").lower()
        verified = data.get("verified") is True
        if status == "success" or verified:
            await query.answer("✅ Payment verified! Crediting wallet...")
            utr = data.get("utr") or ""
            if query.bot:
                await TrueXGatewayService.process_successful_payment(
                    bot=query.bot,
                    order_ref=dep.reference,
                    utr=utr,
                    txg_order_id=txg_order_id
                )
        else:
            msg = data.get("message") or "Waiting for live bank confirmation..."
            await query.answer(f"⏳ Payment not detected yet.\n\nStatus: {msg}\nPlease ensure you transferred exact ₹{dep.amount:.2f}.", show_alert=True)
    except Exception as err:
        logger.error(f"Error checking status for {txg_order_id}: {err}")
        await query.answer("⚠️ Could not verify status right now. Please try again in 5 seconds.", show_alert=True)


@add_fund_router.callback_query(F.data.startswith("txg_cnc:"))
async def handle_txg_cancel_payment(query: CallbackQuery) -> None:
    """Customer clicked Cancel on TrueXGateway payment QR screen."""
    txg_order_id = query.data.split(":")[1]
    await polling_manager.stop_polling(txg_order_id)
    async with AsyncSessionLocal() as db:
        stmt = select(DepositRequest).where(DepositRequest.txg_order_id == txg_order_id)
        dep = (await db.execute(stmt)).scalar_one_or_none()
        if dep and dep.status in ("PENDING", "PENDING_PAYMENT"):
            dep.status = "CANCELLED"
            await db.commit()

    await query.answer("❌ Payment cancelled.")
    user = query.from_user
    user_id = user.id if user else 0
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass
    kb = get_main_reply_keyboard(is_admin=settings.is_admin(user_id))
    if query.message:
        await query.message.answer("❌ Payment cancelled. Returning to main menu.", reply_markup=kb)


@add_fund_router.callback_query(F.data == "cancel_payment")
async def handle_cancel_payment_click(query: CallbackQuery, state: FSMContext) -> None:
    """Customer clicked Back on payment QR screen."""
    await state.clear()
    await query.answer("Payment cancelled.")
    user = query.from_user
    user_id = user.id if user else 0
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass
    kb = get_main_reply_keyboard(is_admin=settings.is_admin(user_id))
    if query.message:
        await query.message.answer("❌ Payment cancelled. Returning to main menu.", reply_markup=kb)


# ==========================================
# 3. Customer Screenshot Upload
# ==========================================
async def _process_customer_screenshot(message: Message, state: FSMContext, file_id: str) -> None:
    """Process customer payment screenshot with single review submission guarantee."""
    user = message.from_user
    if not user:
        return

    data = await state.get_data()
    deposit_ref = data.get("deposit_ref")

    async with AsyncSessionLocal() as db:
        if not deposit_ref:
            pending = await WalletService.get_latest_pending_deposit(db, user.id)
            if pending:
                deposit_ref = pending.reference

        if not deposit_ref:
            await message.answer(
                "⚠️ No active pending payment request found.\n"
                "Please click <b>💰 Add Fund</b> to create a payment request before uploading a screenshot.",
                parse_mode="HTML"
            )
            return

        dep = await WalletService.get_deposit_by_ref(db, deposit_ref)
        if not dep:
            await message.answer("⚠️ Payment request not found.")
            return

        # Screenshot will go to review once only
        if dep.status == "PENDING_REVIEW":
            await message.answer(
                "⚠️ Your payment screenshot has already been submitted and is currently under review by the administrator. Please wait for verification.",
                parse_mode="HTML"
            )
            return

        if dep.status in ("APPROVED", "REJECTED"):
            await message.answer(
                f"⚠️ Payment request <code>{dep.reference}</code> has already been {dep.status.lower()}.",
                parse_mode="HTML"
            )
            return

        success, msg, deposit = await WalletService.attach_screenshot(db, deposit_ref, file_id)

    if not success or not deposit:
        await message.answer(f"⚠️ {msg}")
        return

    await state.clear()

    # Exact customer acknowledgement
    ack_text = (
        "✅ <b>Payment screenshot received.</b>\n\n"
        "Your payment is awaiting admin verification. Your wallet has not been credited yet.\n\n"
        f"Payment Request: <code>{html.escape(deposit.reference)}</code>\n"
        f"Amount: <code>₹{deposit.amount:,.2f}</code>"
    )
    await message.answer(ack_text, parse_mode="HTML")

    # Forward to all configured admin chat IDs reliably
    admin_ids = settings.admin_ids
    logger.info(f"Forwarding payment screenshot for {deposit.reference} to admin IDs: {admin_ids}")
    cust_display = f"@{user.username}" if user.username else (user.first_name or f"User {user.id}")
    submitted_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    admin_caption = (
        "🔔 <b>NEW PAYMENT FOR VERIFICATION</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>Request ID:</b> <code>{html.escape(deposit.reference)}</code>\n"
        f"👤 <b>Customer:</b> {html.escape(cust_display)} (<code>{user.id}</code>)\n"
        f"💰 <b>Requested Amount:</b> <code>₹{deposit.amount:,.2f}</code>\n"
        f"🕒 <b>Submitted At:</b> <code>{submitted_time}</code>\n"
        "📌 <b>Status:</b> <code>PENDING_REVIEW</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Review the attached screenshot and verify against official bank records before approving."
    )
    kb = get_admin_deposit_review_keyboard(deposit.reference)

    for admin_chat_id in admin_ids:
        try:
            if message.bot:
                await message.bot.send_photo(
                    chat_id=admin_chat_id,
                    photo=file_id,
                    caption=admin_caption,
                    parse_mode="HTML",
                    reply_markup=kb
                )
                logger.info(f"Payment screenshot successfully sent to admin {admin_chat_id}")
        except Exception as err:
            logger.error(f"Failed to send_photo to admin {admin_chat_id}: {err}")
            try:
                if message.bot:
                    await message.bot.send_message(
                        chat_id=admin_chat_id,
                        text=admin_caption + "\n\n⚠️ <i>(Screenshot attachment could not be rendered; view in Admin Panel Pending Payments)</i>",
                        parse_mode="HTML",
                        reply_markup=kb
                    )
            except Exception as err2:
                logger.error(f"Fallback send_message failed to admin {admin_chat_id}: {err2}")


@add_fund_router.message(AddFundState.waiting_for_screenshot, F.photo)
@add_fund_router.message(AddFundState.waiting_for_amount, F.photo)
@add_fund_router.message(F.photo)
async def handle_payment_screenshot_photo(message: Message, state: FSMContext) -> None:
    """Inform customer that manual screenshot upload is not needed; payments auto-detect."""
    user = message.from_user
    if not user:
        return
    await message.answer(
        "ℹ️ <b>Payment Screenshot Not Needed!</b>\n\n"
        "Payments made via our dynamic UPI QR code are <b>automatically detected & credited</b> within seconds.\n\n"
        "Please ensure you transferred the <b>exact decimal amount</b> shown on your QR code. Your balance will update automatically upon bank confirmation.",
        parse_mode="HTML"
    )


@add_fund_router.message(AddFundState.waiting_for_screenshot, F.document)
@add_fund_router.message(AddFundState.waiting_for_amount, F.document)
@add_fund_router.message(F.document)
async def handle_payment_screenshot_document(message: Message, state: FSMContext) -> None:
    """Inform customer that manual screenshot upload is not needed."""
    doc = message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        await message.answer(
            "ℹ️ <b>Payment Screenshot Not Needed!</b>\n\n"
            "Payments made via our dynamic UPI QR code are <b>automatically detected & credited</b> within seconds.\n\n"
            "Please ensure you transferred the <b>exact decimal amount</b> shown on your QR code. Your balance will update automatically upon bank confirmation.",
            parse_mode="HTML"
        )


@add_fund_router.callback_query(F.data.startswith("chk_dep:"))
async def handle_check_deposit_status(query: CallbackQuery) -> None:
    """Customer checks live status of their payment request."""
    ref = query.data.split(":")[1]
    async with AsyncSessionLocal() as db:
        dep = await WalletService.get_deposit_by_ref(db, ref)

    if not dep:
        await query.answer("Payment request not found.", show_alert=True)
        return

    status_icon = "⏳"
    if dep.status == "APPROVED":
        status_icon = "✅"
    elif dep.status == "REJECTED":
        status_icon = "❌"
    elif dep.status == "PENDING_REVIEW":
        status_icon = "🔍"

    text = (
        f"📋 *PAYMENT STATUS: {dep.reference}*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Amount:* `₹{dep.amount:,.2f}`\n"
        f"{status_icon} *Status:* `{dep.status}`\n"
        f"🕒 *Created:* `{dep.created_at.strftime('%Y-%m-%d %H:%M UTC')}`\n"
    )
    if dep.admin_notes:
        text += f"📝 *Note:* `{dep.admin_notes}`\n"

    await query.answer("Status refreshed!")
    if query.message:
        await query.message.edit_caption(
            caption=text,
            parse_mode="Markdown",
            reply_markup=get_deposit_request_keyboard(dep.reference)
        )


# ==========================================
# 4. Admin Payment Approval and Rejection
# ==========================================
@add_fund_router.callback_query(F.data.startswith("adm_app_pay:"))
async def handle_admin_approve_payment(query: CallbackQuery) -> None:
    """
    Authorized administrator approves payment request.
    Atomically updates status, credits wallet, writes ledger, and notifies customer.
    """
    user = query.from_user
    user_id = user.id if user else 0
    if not _is_admin(user_id):
        await query.answer("⛔ Unauthorized: Admin access required.", show_alert=True)
        return

    ref = query.data.split(":")[1]
    async with AsyncSessionLocal() as db:
        success, msg, deposit = await WalletService.approve_deposit(
            db=db,
            deposit_ref=ref,
            admin_id=user_id,
            admin_notes=f"Approved by Admin {user_id}"
        )

    if not success:
        await query.answer(f"⚠️ {msg}", show_alert=True)
        return

    await query.answer("✅ Payment approved and wallet credited!", show_alert=True)

    # Update admin message caption
    if query.message:
        updated_caption = (
            f"✅ *PAYMENT APPROVED*\n"
            f"Request: `{deposit.reference}`\n"
            f"Amount Credited: `₹{deposit.amount:,.2f}`\n"
            f"Customer: `{deposit.telegram_user_id}`\n"
            f"Verified by Admin: `{user_id}`"
        )
        try:
            await query.message.edit_caption(caption=updated_caption, parse_mode="Markdown", reply_markup=None)
        except Exception:
            pass

    # Notify customer exactly as requested in Part 12
    if query.bot and deposit:
        cust_msg = (
            "✅ *Payment Approved*\n\n"
            f"Amount added: `₹{deposit.amount:,.2f}`\n\n"
            "Your wallet has been updated successfully.\n\n"
            f"Payment Request: `{deposit.reference}`"
        )
        try:
            await query.bot.send_message(
                chat_id=deposit.telegram_user_id,
                text=cust_msg,
                parse_mode="Markdown"
            )
        except Exception:
            pass


@add_fund_router.callback_query(F.data.startswith("adm_rej_pay:"))
async def handle_admin_reject_payment(query: CallbackQuery) -> None:
    """Authorized administrator rejects payment request."""
    user = query.from_user
    user_id = user.id if user else 0
    if not _is_admin(user_id):
        await query.answer("⛔ Unauthorized: Admin access required.", show_alert=True)
        return

    ref = query.data.split(":")[1]
    reason = "Payment could not be verified."

    async with AsyncSessionLocal() as db:
        success, msg, deposit = await WalletService.reject_deposit(
            db=db,
            deposit_ref=ref,
            admin_id=user_id,
            reason=reason
        )

    if not success:
        await query.answer(f"⚠️ {msg}", show_alert=True)
        return

    await query.answer("❌ Payment rejected.", show_alert=True)

    # Update admin message caption
    if query.message:
        updated_caption = (
            f"❌ *PAYMENT REJECTED*\n"
            f"Request: `{deposit.reference}`\n"
            f"Customer: `{deposit.telegram_user_id}`\n"
            f"Reason: `{reason}`\n"
            f"Handled by Admin: `{user_id}`"
        )
        try:
            await query.message.edit_caption(caption=updated_caption, parse_mode="Markdown", reply_markup=None)
        except Exception:
            pass

    # Notify customer exactly as requested in Part 12
    if query.bot and deposit:
        cust_msg = (
            "❌ *Payment Request Rejected*\n\n"
            f"Reason: {reason}\n\n"
            "Your wallet has not been credited. Contact the admin if you believe this is an error."
        )
        try:
            await query.bot.send_message(
                chat_id=deposit.telegram_user_id,
                text=cust_msg,
                parse_mode="Markdown"
            )
        except Exception:
            pass


# ==========================================
# 5. Customer 📜 My Transactions & 👛 My Wallet
# ==========================================
@add_fund_router.message(F.text == "📜 My Transactions")
@add_fund_router.message(Command("transactions"))
@add_fund_router.message(Command("history"))
async def handle_my_transactions_menu(message: Message) -> None:
    """Display customer wallet balance, deposit history, and purchase history."""
    user = message.from_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)
        deposits = await WalletService.get_customer_deposit_requests(db, user_id, limit=6)
        txs = await WalletService.get_recent_transactions(db, user_id, limit=6)

    lines = [
        "📜 *YOUR TRANSACTION HISTORY*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💳 *Current Wallet Balance:* `₹{balance:,.2f}`\n",
        "📥 *Recent Deposit Requests:*"
    ]

    if deposits:
        for d in deposits:
            st_badge = "⏳" if "PENDING" in d.status else ("✅" if d.status == "APPROVED" else "❌")
            date_str = d.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"• `{d.reference}` | `₹{d.amount:,.2f}` | {st_badge} `{d.status}`\n  🕒 {date_str}")
    else:
        lines.append("• No deposit requests recorded.")

    lines.append("\n🛒 *Recent Purchases:*")
    purchases = [t for t in txs if t.tx_type == "PURCHASE"]
    if purchases:
        for p in purchases:
            date_str = p.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"• `{p.reference}` | `-₹{abs(p.amount):,.2f}`\n  🕒 {date_str}")
    else:
        lines.append("• No purchase transactions recorded.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund"),
            InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo")
        ],
        [InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main")]
    ])
    await message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


@add_fund_router.callback_query(F.data.in_({"my_transactions", "my_wallet_history"}))
async def handle_my_transactions_callback(query: CallbackQuery) -> None:
    """Handle History button click from Add Fund or Wallet."""
    await query.answer()
    user = query.from_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)
        deposits = await WalletService.get_customer_deposit_requests(db, user_id, limit=6)
        txs = await WalletService.get_recent_transactions(db, user_id, limit=6)

    lines = [
        "📜 *YOUR TRANSACTION HISTORY*",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💳 *Current Wallet Balance:* `₹{balance:,.2f}`\n",
        "📥 *Recent Deposit Requests:*"
    ]

    if deposits:
        for d in deposits:
            st_badge = "⏳" if "PENDING" in d.status else ("✅" if d.status == "APPROVED" else "❌")
            date_str = d.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"• `{d.reference}` | `₹{d.amount:,.2f}` | {st_badge} `{d.status}`\n  🕒 {date_str}")
    else:
        lines.append("• No deposit requests recorded.")

    lines.append("\n🛒 *Recent Purchases:*")
    purchases = [t for t in txs if t.tx_type == "PURCHASE"]
    if purchases:
        for p in purchases:
            date_str = p.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"• `{p.reference}` | `-₹{abs(p.amount):,.2f}`\n  🕒 {date_str}")
    else:
        lines.append("• No purchase transactions recorded.")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund", style="primary"),
            InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo", style="primary")
        ],
        [InlineKeyboardButton(text="« Back", callback_data="goto_add_fund", style="danger")]
    ])
    if query.message:
        try:
            await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
        except Exception:
            await query.message.answer("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


async def _send_my_wallet_info(target: Message | CallbackQuery, user_id: int) -> None:
    """Helper to display customer wallet overview."""
    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)
        txs = await WalletService.get_recent_transactions(db, user_id, limit=5)

    lines = [
        "👛 *MY WALLET*",
        "━━━━━━━━━━━━━━━━━━",
        f"💳 *Available Balance:* `₹{balance:,.2f}`",
        f"👤 *Customer ID:* `{user_id}`\n",
        "📜 *Recent Ledger Activity:*"
    ]

    if txs:
        for t in txs:
            sign = "+" if t.amount > 0 else ""
            date_str = t.created_at.strftime("%Y-%m-%d %H:%M")
            lines.append(f"• `{date_str}` | *{t.tx_type}* `{sign}₹{abs(t.amount):,.2f}`\n  Ref: `{t.reference}` (Bal: `₹{t.balance_after:,.2f}`)")
    else:
        lines.append("• No recent transactions recorded.")

    lines.append("\n━━━━━━━━━━━━━━━━━━")
    lines.append("Use the options below to top up or inspect purchases:")

    keyboard = get_my_wallet_keyboard()

    if isinstance(target, CallbackQuery):
        await target.answer("Wallet refreshed!")
        if target.message:
            await target.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)
    else:
        await target.answer("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


@add_fund_router.message(F.text == "👛 My Wallet")
@add_fund_router.message(Command("wallet"))
async def handle_my_wallet_menu(message: Message) -> None:
    """Display customer wallet overview."""
    user = message.from_user
    user_id = user.id if user else 0
    await _send_my_wallet_info(message, user_id)


@add_fund_router.callback_query(F.data == "refresh_my_wallet")
async def handle_refresh_my_wallet(query: CallbackQuery) -> None:
    """Refresh customer wallet overview in place."""
    user = query.from_user
    user_id = user.id if user else 0
    await _send_my_wallet_info(query, user_id)


@add_fund_router.callback_query(F.data == "orders_list")
async def handle_orders_list_redirect(query: CallbackQuery) -> None:
    """Redirect to customer order list from wallet screen."""
    from indo_selling_bot.services.order_service import OrderService
    user_id = query.from_user.id
    async with AsyncSessionLocal() as db:
        orders = await OrderService.get_customer_orders(db, user_id, limit=5)

    if not orders:
        await query.answer("ℹ️ You have not placed any orders yet.", show_alert=True)
        return

    await query.answer()
    lines = [
        "📦 *YOUR RECENT ORDERS*",
        "━━━━━━━━━━━━━━━━━━"
    ]
    buttons = []
    for ord in orders:
        lines.append(f"• `{ord.order_code}` | {ord.total_amount:,.0f} {ord.currency} [{ord.status}]")
        buttons.append([
            InlineKeyboardButton(text=f"🧾 View {ord.order_code}", callback_data=f"chk_ord:{ord.order_code}")
        ])
    buttons.append([InlineKeyboardButton(text="« Back to Wallet", callback_data="refresh_my_wallet")])

    if query.message:
        await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
