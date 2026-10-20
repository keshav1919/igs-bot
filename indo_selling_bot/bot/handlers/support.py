"""
Support ticketing handlers for Telegram Bot customers.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.support_service import SupportService

support_router = Router()

class SupportFSM(StatesGroup):
    waiting_for_message = State()

@support_router.message(F.text == "💬 Customer Support")
@support_router.message(Command("support"))
async def handle_support_cmd(message: Message, state: FSMContext) -> None:
    """Prompt user to describe their issue or inquiry."""
    await state.set_state(SupportFSM.waiting_for_message)
    await state.update_data(order_code=None)

    prompt = (
        "💬 *INDO COMMERCE CUSTOMER SUPPORT*\n\n"
        "Please reply with a description of your question or issue.\n"
        "If your question concerns a specific order, please mention the Order Code."
    )
    await message.answer(prompt, parse_mode="Markdown")

@support_router.callback_query(F.data.startswith("ord_sup:"))
async def handle_order_support_btn(callback: CallbackQuery, state: FSMContext) -> None:
    """Open support inquiry for a specific order."""
    order_code = callback.data.split(":")[1]
    await state.set_state(SupportFSM.waiting_for_message)
    await state.update_data(order_code=order_code)

    prompt = (
        f"💬 *SUPPORT FOR ORDER:* `{order_code}`\n\n"
        f"Please reply with your question or inquiry regarding this order. "
        f"Our team will respond directly here."
    )
    await callback.message.answer(prompt, parse_mode="Markdown")
    await callback.answer()

@support_router.message(SupportFSM.waiting_for_message, F.text)
async def handle_support_message_received(message: Message, state: FSMContext) -> None:
    """Save customer support message to database."""
    user = message.from_user
    data = await state.get_data()
    order_code = data.get("order_code")
    body = message.text.strip()

    async with AsyncSessionLocal() as db:
        ticket = await SupportService.create_ticket(
            db=db,
            telegram_user_id=user.id,
            subject=f"Inquiry for {order_code}" if order_code else f"Customer Support - {user.first_name}",
            message=body,
            telegram_username=user.username,
            order_code=order_code
        )

    await state.clear()

    reply = (
        f"✅ *SUPPORT TICKET OPENED: `{ticket.ticket_code}`*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Thank you for contacting us. An administrator has received your message "
        f"and will reply directly to this chat as soon as possible."
    )
    await message.answer(reply, parse_mode="Markdown")
