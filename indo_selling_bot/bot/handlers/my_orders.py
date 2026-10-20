"""
Customer order status tracking and fulfillment retrieval handler.
Enforces customer data isolation: users cannot access orders belonging to others.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.order_service import OrderService
from indo_selling_bot.models.order import OrderStatus
from indo_selling_bot.bot.keyboards import get_order_actions_keyboard

my_orders_router = Router()

@my_orders_router.message(F.text == "📦 My Orders")
@my_orders_router.message(Command("orders"))
async def handle_list_my_orders(message: Message) -> None:
    """List recent orders belonging to the authenticated customer."""
    user_id = message.from_user.id
    async with AsyncSessionLocal() as db:
        orders = await OrderService.get_customer_orders(db, user_id, limit=10)

    if not orders:
        await message.answer("ℹ️ You have not placed any orders yet. Click *🛍️ Browse Catalog* to get started!")
        return

    buttons = []
    for ord in orders:
        icon = "⏳" if ord.status == OrderStatus.PENDING_PAYMENT else (
            "🔍" if ord.status == OrderStatus.PAYMENT_SUBMITTED else (
                "✅" if ord.status in (OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED) else "❌"
            )
        )
        buttons.append([
            InlineKeyboardButton(
                text=f"{icon} {ord.order_code} | {ord.total_amount:,.0f} {ord.currency} [{ord.status}]",
                callback_data=f"chk_ord:{ord.order_code}"
            )
        ])

    text = "📦 *YOUR RECENT ORDERS:*\nSelect an order below to check live status or retrieve verified deliverables:"
    await message.answer(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))

@my_orders_router.callback_query(F.data.startswith("chk_ord:"))
async def handle_check_order_detail(callback: CallbackQuery) -> None:
    """View details of a specific order. Enforces authorization check."""
    order_code = callback.data.split(":")[1]
    user_id = callback.from_user.id

    async with AsyncSessionLocal() as db:
        order = await OrderService.get_by_code(db, order_code)

    if not order:
        await callback.answer("Order not found.", show_alert=True)
        return

    # Security check: Ensure order belongs to caller
    if order.telegram_user_id != user_id:
        await callback.answer("⛔ Unauthorized: You do not have permission to view this order.", show_alert=True)
        return

    # Build status description
    status_emoji = {
        OrderStatus.PENDING_PAYMENT: "⏳ Pending Payment",
        OrderStatus.PAYMENT_SUBMITTED: "🔍 In Review (Admin Verification Pending)",
        OrderStatus.PAID_VERIFIED: "✅ Paid & Verified",
        OrderStatus.REJECTED: "❌ Payment Rejected",
        OrderStatus.CANCELLED: "🚫 Cancelled"
    }.get(order.status, order.status)

    text = (
        f"🧾 *ORDER DETAILS:* `{order.order_code}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Status:* `{status_emoji}`\n"
        f"🔹 *Total Amount:* `{order.total_amount:,.0f} {order.currency}`\n"
        f"🔹 *Created:* {order.created_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
    )

    if order.tx_reference:
        text += f"🔹 *Submitted Tx Ref:* `{order.tx_reference}`\n"

    if order.verified_by_admin:
        text += f"🔹 *Verified By:* Admin `{order.verified_by_admin}`\n"

    # Show items
    text += "\n📦 *Items:*\n"
    for item in order.items:
        text += f"• {item.item_title} (x{item.quantity}) - {item.subtotal:,.0f} {order.currency}\n"

    # If verified and fulfillment released, display the goods
    if order.fulfillment_released and order.fulfillment_data:
        text += (
            f"\n🎉 *VERIFIED DELIVERABLES / FULFILLMENT:*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{order.fulfillment_data}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Thank you for your purchase!"
        )
    elif order.status == OrderStatus.PAYMENT_SUBMITTED:
        text += "\n⏳ *Awaiting Verification:* Our administrators are checking your transaction. Deliverables will appear here upon verification."
    elif order.status == OrderStatus.REJECTED:
        text += f"\n❌ *Note from Admin:* {order.verification_notes or 'Payment proof could not be verified.'}"

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_order_actions_keyboard(order.order_code, order.status)
    )
    await callback.answer()
