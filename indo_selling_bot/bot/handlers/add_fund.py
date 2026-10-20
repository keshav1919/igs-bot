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

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.payment_setting_service import PaymentSettingService
from indo_selling_bot.bot.keyboards import (
    get_add_fund_keyboard,
    get_deposit_request_keyboard,
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


def _is_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    return settings.is_admin(user_id)


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
async def _send_add_fund_info(target: Message | CallbackQuery, user_id: int) -> None:
    """Helper to display business payment instructions and ₹30 minimum requirement."""
    async with AsyncSessionLocal() as db:
        balance = await WalletService.get_balance(db, user_id)

    min_dep = int(settings.MIN_DEPOSIT_AMOUNT)

    text = (
        "💰 Add Funds to Your Wallet\n\n\n"
        f"⚠️ Minimum Deposit: ₹{min_dep}\n"
        f"💳 Current Wallet Balance: ₹{balance:,.2f}\n\n"
        "📋 How to Pay:\n"
        "1. Scan the QR code .\n"
        "2. Complete the Payment.\n"
        "3. Upload a payment screenshot.\n"
        "4. Funds are credited .\n\n"
        "Click ➕  Add Fund   to generate your QR:"
    )

    keyboard = get_add_fund_keyboard()

    if isinstance(target, CallbackQuery):
        await target.answer()
        if target.message:
            await target.message.edit_text(text, reply_markup=keyboard)
    else:
        await target.answer(text, reply_markup=keyboard)


@add_fund_router.message(F.text == "💰 Add Fund")
@add_fund_router.message(Command("addfund"))
@add_fund_router.message(Command("fund"))
async def handle_add_fund_menu(message: Message, state: FSMContext) -> None:
    """Entry point for customer adding funds."""
    await state.clear()
    user = message.from_user
    user_id = user.id if user else 0
    await _send_add_fund_info(message, user_id)


@add_fund_router.callback_query(F.data == "goto_add_fund")
async def handle_goto_add_fund_callback(query: CallbackQuery, state: FSMContext) -> None:
    """Redirect to Add Fund from insufficient balance button."""
    await state.clear()
    user = query.from_user
    user_id = user.id if user else 0
    await _send_add_fund_info(query, user_id)


# ==========================================
# 2. Enter Deposit Amount & Generate Dynamic QR
# ==========================================
@add_fund_router.callback_query(F.data == "fund_enter_amount")
async def handle_fund_enter_amount_click(query: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type deposit amount."""
    await query.answer()
    await state.set_state(AddFundState.waiting_for_amount)
    min_dep = int(settings.MIN_DEPOSIT_AMOUNT)
    prompt_text = (
        "💵 *Enter Deposit Amount*\n\n"
        f"Please reply with the exact amount you wish to deposit in ₹ (e.g., `30`, `50`, `100`, `500`).\n\n"
        f"⚠️ *Minimum deposit: ₹{min_dep}*\n"
        "_Send /cancel to abort at any time._"
    )
    if query.message:
        await query.message.answer(prompt_text, parse_mode="Markdown")


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
    Strictly enforces:
    - ₹29 and below is rejected with: '⚠️ Minimum Add Fund Amount is ₹30. Please enter ₹30 or more.'
    - ₹30 and above is accepted.
    - Generates dynamic QR code and sets PENDING_PAYMENT status.
    """
    raw_text = (message.text or "").strip().replace(",", "").replace("₹", "").replace("INR", "").replace("Rp", "").strip()
    min_dep = int(settings.MIN_DEPOSIT_AMOUNT)

    try:
        amount = int(float(raw_text))
        if amount <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        await message.answer(f"⚠️ Minimum Add Fund Amount is ₹{min_dep}. Please enter ₹{min_dep} or more.")
        return

    # Strictly reject below minimum
    if amount < min_dep:
        await message.answer(f"⚠️ Minimum Add Fund Amount is ₹{min_dep}. Please enter ₹{min_dep} or more.")
        return

    # Reject excessive deposit
    if amount > int(settings.MAX_DEPOSIT_AMOUNT):
        await message.answer(f"⚠️ Maximum single deposit amount is ₹{int(settings.MAX_DEPOSIT_AMOUNT):,}. Please enter a smaller amount.")
        return

    user = message.from_user
    if not user:
        await message.answer("User context missing.")
        return

    # Create DepositRequest in DB
    async with AsyncSessionLocal() as db:
        deposit = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=user.id,
            amount=amount,
            currency="₹",
            username=user.username,
            first_name=user.first_name,
            initial_status="PENDING_PAYMENT"
        )
        payment_info = await PaymentSettingService.get_settings(db)
        await db.commit()

    upi_id = payment_info.get("upi_id") or settings.PAYMENT_UPI_ID
    payee_name = payment_info.get("payee_name") or settings.DEFAULT_UPI_NAME

    # Save active reference in state and transition to waiting for screenshot immediately
    await state.set_state(AddFundState.waiting_for_screenshot)
    await state.update_data(deposit_ref=deposit.reference)

    # Generate dynamic UPI QR image bytes
    qr_bytes = generate_upi_qr_bytes(
        upi_id=upi_id,
        payee_name=payee_name,
        amount=float(amount),
        ref_id=deposit.reference
    )
    qr_file = BufferedInputFile(qr_bytes, filename=f"qr_{deposit.reference}.png")

    caption = (
        f"💳 <b>PAYMENT REQUEST:</b> <code>{deposit.reference}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Amount to Pay:</b> <code>₹{amount:,.2f}</code>\n"
        f"🆔 <b>Official UPI ID:</b> <code>{upi_id}</code>\n"
        f"👤 <b>Payee Name:</b> <code>{payee_name}</code>\n"
        "⏳ <b>Status:</b> <code>PENDING PAYMENT</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. Scan the dynamic QR code above or pay via UPI ID in your UPI app (Paytm, GPay, PhonePe).\n"
        "2. <b>Send your payment screenshot directly in this chat</b> after completing payment.\n\n"
        "⚠️ <i>Notice: Submitting a screenshot does not automatically credit money to your wallet. "
        "Every deposit is manually verified by the administrator against bank records.</i>"
    )

    kb = get_deposit_request_keyboard(deposit.reference)
    await message.answer_photo(photo=qr_file, caption=caption, parse_mode="HTML", reply_markup=kb)


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
    """Process customer uploaded payment screenshot photo."""
    photo = message.photo[-1]
    await _process_customer_screenshot(message, state, photo.file_id)


@add_fund_router.message(AddFundState.waiting_for_screenshot, F.document)
@add_fund_router.message(AddFundState.waiting_for_amount, F.document)
@add_fund_router.message(F.document)
async def handle_payment_screenshot_document(message: Message, state: FSMContext) -> None:
    """Process customer uploaded payment screenshot image document."""
    doc = message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        await _process_customer_screenshot(message, state, doc.file_id)


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
