"""
Order creation and verified payment workflow handlers for Telegram Bot.
Enforces the strict rule: Never treat screenshots as verified confirmation;
Releases occur only after authorized administrative review.
"""
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.order_service import OrderService
from indo_selling_bot.services.payment_service import PaymentService
from indo_selling_bot.bot.keyboards import get_order_actions_keyboard

order_router = Router()

class OrderFSM(StatesGroup):
    waiting_for_tx_ref = State()

@order_router.callback_query(F.data.startswith("buy:"))
async def handle_buy_item(callback: CallbackQuery, state: FSMContext) -> None:
    """Customer initiates order creation for an inventory item."""
    item_id = int(callback.data.split(":")[1])
    user = callback.from_user

    async with AsyncSessionLocal() as db:
        order, err = await OrderService.create_order(
            db=db,
            telegram_user_id=user.id,
            item_id=item_id,
            quantity=1,
            telegram_username=user.username,
            telegram_first_name=user.first_name,
            payment_method="MANUAL_TRANSFER"
        )
        if err and not order:
            await callback.answer(f"❌ {err}", show_alert=True)
            return

        # Prepare instructions
        instructions = await PaymentService.format_instructions_for_telegram(
            db=db,
            order_code=order.order_code,
            total_amount=order.total_amount,
            currency=order.currency
        )

    order_card = (
        f"🎉 *ORDER CREATED SUCCESSFULLY!*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧾 *Order Code:* `{order.order_code}`\n"
        f"💰 *Amount Due:* `{order.total_amount:,.0f} {order.currency}`\n"
        f"⏳ *Status:* `PENDING PAYMENT`\n\n"
        f"{instructions}"
    )

    await callback.message.answer(
        order_card,
        parse_mode="Markdown",
        reply_markup=get_order_actions_keyboard(order.order_code, order.status)
    )
    await callback.answer("Order initiated!")

@order_router.callback_query(F.data.startswith("pay_ref:"))
async def handle_submit_pay_ref_btn(callback: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to send their payment transaction reference."""
    order_code = callback.data.split(":")[1]
    await state.update_data(active_order_code=order_code)
    await state.set_state(OrderFSM.waiting_for_tx_ref)

    prompt = (
        f"📝 *SUBMIT TRANSACTION REFERENCE FOR ORDER:* `{order_code}`\n\n"
        f"Please reply with your **Transaction Hash (TxID)**, **Bank Reference Number**, or **Sender Account Name**.\n\n"
        f"⚠️ *Important Security Rule:*\n"
        f"Screenshots are not automatically accepted as verification. "
        f"You must provide the exact reference string for manual administrative audit."
    )
    await callback.message.answer(prompt, parse_mode="Markdown")
    await callback.answer()

@order_router.message(OrderFSM.waiting_for_tx_ref, F.photo)
async def handle_screenshot_uploaded(message: Message) -> None:
    """
    Handling screenshots: As per specification, never treat customer-uploaded
    screenshots as payment confirmation. Politely instruct user to provide transaction reference string.
    """
    warn_text = (
        "⚠️ *Screenshot Received & Logged for Audit*\n\n"
        "However, per store security policy, **screenshots are NEVER automatically treated as payment confirmation**.\n\n"
        "👉 Please **type and send the transaction reference string** (e.g., Bank Transfer Ref, Sender Name, or Crypto TxID) "
        "as text below so our administrators can verify it."
    )
    await message.answer(warn_text, parse_mode="Markdown")

@order_router.message(OrderFSM.waiting_for_tx_ref, F.text)
async def handle_tx_reference_text(message: Message, state: FSMContext) -> None:
    """Process customer's submitted text reference."""
    data = await state.get_data()
    order_code = data.get("active_order_code")
    tx_reference = message.text.strip()

    if not order_code:
        await message.answer("⚠️ Session expired. Please view your order via *My Orders* and submit again.")
        await state.clear()
        return

    if len(tx_reference) < 3:
        await message.answer("❌ Invalid reference. Please provide a valid transaction ID or account name.")
        return

    async with AsyncSessionLocal() as db:
        success, msg = await OrderService.submit_payment_reference(
            db=db,
            order_code=order_code,
            telegram_user_id=message.from_user.id,
            tx_reference=tx_reference
        )

    await state.clear()

    if not success:
        await message.answer(f"❌ Submission failed: {msg}")
        return

    confirmation = (
        f"✅ *PAYMENT REFERENCE RECORDED!*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧾 *Order Code:* `{order_code}`\n"
        f"🔗 *Reference Submitted:* `{tx_reference}`\n"
        f"⏳ *Status:* `PAYMENT SUBMITTED (Pending Admin Review)`\n\n"
        f"An authorized administrator will verify your transaction against bank/blockchain records. "
        f"Upon approval, your deliverables will be released instantly here!"
    )
    await message.answer(
        confirmation,
        parse_mode="Markdown",
        reply_markup=get_order_actions_keyboard(order_code, "PAYMENT_SUBMITTED")
    )
