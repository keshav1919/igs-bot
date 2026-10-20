"""
Buy Indo Telegram Flow.
Displays available age categories, shows stock count and quantity selector (1 igs, 5 igs, 10 igs, 20 igs, custom amount),
checks wallet balance, atomically reserves inventory directly from stock.txt, deducts wallet balance once,
delivers account credentials in requested format, logs sold items to sold.txt,
and notifies admin with remaining stock in .txt format.
"""
import time
import html
import logging
import asyncio
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.stock_txt_service import StockTxtService
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.pricing_service import PricingService
from indo_selling_bot.models.order import Order, OrderItem
from indo_selling_bot.bot.keyboards import (
    get_available_buy_categories_keyboard,
    get_buy_quantity_keyboard,
)
from indo_selling_bot.config import settings

logger = logging.getLogger(__name__)

buy_indo_router = Router()

class BuyIndoState(StatesGroup):
    waiting_for_custom_quantity = State()


# In-flight request tracking with mutex & failsafe timeout to prevent duplicate button clicks
_user_buy_in_flight: dict[int, float] = {}
_buy_lock_mutex = asyncio.Lock()
BUY_LOCK_TIMEOUT_SECONDS = 3.0

@asynccontextmanager
async def buy_click_guard(user_id: int, query: CallbackQuery | None = None):
    """
    Prevents duplicate or concurrent button clicks for a user across the Indo buy flow.
    Rejects spam, rapid double-clicks, or clicking another button while an operation is processing.
    """
    now = time.monotonic()
    async with _buy_lock_mutex:
        if user_id in _user_buy_in_flight:
            start_time = _user_buy_in_flight[user_id]
            if now - start_time < BUY_LOCK_TIMEOUT_SECONDS:
                if query:
                    try:
                        await query.answer("⏳ Processing, please wait...", show_alert=False)
                    except Exception:
                        pass
                yield False
                return
            else:
                logger.warning(f"Cleared expired buy lock for user {user_id}")

        _user_buy_in_flight[user_id] = now

    try:
        yield True
    finally:
        async with _buy_lock_mutex:
            _user_buy_in_flight.pop(user_id, None)


@buy_indo_router.callback_query(F.data == "noop")
async def handle_noop_callback(query: CallbackQuery) -> None:
    """Handle inert / disabled button clicks."""
    await query.answer("⏳ Processing your request, please wait...", show_alert=False)


def format_delivery_message(accounts: list[dict], day_number: int, remaining_stock: int) -> str:
    """Format delivery message matching user exact template with HTML tap-to-copy code tags."""
    count = len(accounts)
    unique_domains = []
    for acc in accounts:
        d = acc.get("domain") or (acc.get("email", "").split("@")[-1] if "@" in acc.get("email", "") else "metamail.store")
        if d not in unique_domains:
            unique_domains.append(d)
    domain_str = " , ".join(unique_domains) if unique_domains else "metamail.store"

    period_str = "1 day old" if day_number == 1 else f"{day_number} days old"

    if count == 1:
        mail_cmd = f"/accept {accounts[0]['email']}"
    else:
        mail_cmd = f"/bulkaccept {' '.join(acc['email'] for acc in accounts)}"

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        f"📦 INDO STOCK DELIVERY ({count} Accounts)",
        "━━━━━━━━━━━━━━━━━━",
        f"🌐 Domain: {domain_str}",
        f"📅 Period: {period_str}",
        "",
        "===============",
        "How to Get mail Acess : ",
        "Got To Mail Bot - @LegMacosbot",
        f"Pase this : <code>{html.escape(mail_cmd)}</code>",
        "===============",
        "",
    ]

    for idx, acc in enumerate(accounts, start=1):
        uname = acc.get("username") or ""
        email_val = acc.get("email", "")
        pass_val = acc.get("password", "")
        lines.append(f"#{idx}")
        lines.append(f"📧 Email :- <code>{html.escape(email_val)}</code>")
        if uname:
            lines.append(f"🫟 UserName : - <code>{html.escape(uname)}</code>")
        else:
            lines.append("🫟 UserName : - ")
        lines.append(f"🔑 Password : <code>{html.escape(pass_val)}</code>")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("📋 Quick Copy (Email:Password):")
    for acc in accounts:
        lines.append(f"<code>{html.escape(acc['email'])}:{html.escape(acc['password'])}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"📦 Remaining Stock: {remaining_stock} ")
    lines.append("💾 Have a Nice day reg @legend_tg ")

    return "\n".join(lines)


@buy_indo_router.message(F.text == "🛒 Buy Indo")
@buy_indo_router.message(Command("buy"))
async def handle_buy_indo_menu(message: Message, state: FSMContext | None = None) -> None:
    """Display only available age categories with genuine stock from stock.txt."""
    if state:
        await state.clear()

    available_cats = StockTxtService.get_available_categories()

    if not available_cats:
        text = "📦 All categories are currently out of stock. Please check again later."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")]
        ])
    else:
        text = "🛒  Select  Indo"
        kb = get_available_buy_categories_keyboard(available_cats)

    await message.answer(
        text,
        reply_markup=kb
    )


@buy_indo_router.callback_query(F.data == "back_to_buy_indo")
async def handle_back_to_buy_indo_callback(query: CallbackQuery, state: FSMContext | None = None) -> None:
    """Return customer to available age categories list."""
    await query.answer()
    if state:
        await state.clear()

    available_cats = StockTxtService.get_available_categories()

    if not available_cats:
        text = "📦 All categories are currently out of stock. Please check again later."
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")]
        ])
    else:
        text = "🛒  Select  Indo"
        kb = get_available_buy_categories_keyboard(available_cats)

    if query.message:
        try:
            await query.message.edit_text(
                text,
                reply_markup=kb
            )
        except Exception:
            pass


@buy_indo_router.callback_query(F.data.startswith("buy_day:"))
async def handle_select_age_category(query: CallbackQuery, state: FSMContext | None = None) -> None:
    """
    Handle customer selecting an available age category.
    Displays stock count from stock.txt and presents quantity selection buttons.
    """
    user = query.from_user
    user_id = user.id if user else 0
    async with buy_click_guard(user_id, query) as ok:
        if not ok:
            return
        if state:
            await state.clear()
        try:
            day_num = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.answer("Invalid category selection.", show_alert=True)
            return

        if day_num < 1:
            await query.answer("Invalid category selection.", show_alert=True)
            return

        # Acknowledge immediately to dismiss Telegram client loading spinner
        await query.answer()

        total_qty = StockTxtService.get_stock_count_for_day(day_num)

        async with AsyncSessionLocal() as db:
            wallet_balance = await WalletService.get_balance(db, user_id)
            price, currency = await PricingService.get_effective_price(db, day_number=day_num)

        period_label = "1 day old" if day_num == 1 else f"{day_num} days old"

        if total_qty > 0:
            text = (
                f"# {period_label} Igs\n\n"
                f"💰 Price: ₹{price:,.0f} /- per \n"
                f"💳 Balance: ₹{wallet_balance:,.0f}\n"
                f"📊 Total Stock : {total_qty} igs\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Select quantity to buy:"
            )
            keyboard = get_buy_quantity_keyboard(day_num)
        else:
            # Stock ran out while user was browsing
            available_cats = StockTxtService.get_available_categories()

            if not available_cats:
                text = (
                    f"# {period_label} Igs\n\n"
                    "⚠️ Availability has changed. This category is now out of stock.\n\n"
                    "📦 All categories are currently out of stock. Please check again later."
                )
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")]
                ])
            else:
                text = (
                    f"# {period_label} Igs\n\n"
                    "⚠️ Availability has changed. This category is now out of stock.\n"
                    "Please choose another available category below:"
                )
                keyboard = get_available_buy_categories_keyboard(available_cats)

        if query.message:
            try:
                await query.message.edit_text(text, reply_markup=keyboard)
            except Exception:
                pass


@buy_indo_router.callback_query(F.data.startswith("buy_qty:"))
async def handle_buy_preset_quantity(query: CallbackQuery) -> None:
    """Handle user clicking preset quantity button: 1 igs, 5 igs, 10 igs, 20 igs."""
    user = query.from_user
    user_id = user.id if user else 0
    async with buy_click_guard(user_id, query) as ok:
        if not ok:
            return

        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Invalid quantity selection.", show_alert=True)
            return

        try:
            day_num = int(parts[1])
            qty = int(parts[2])
        except ValueError:
            await query.answer("Malformed parameters.", show_alert=True)
            return

        total_qty = StockTxtService.get_stock_count_for_day(day_num)
        if total_qty < qty:
            period_label = "1 day old" if day_num == 1 else f"{day_num} days old"
            await query.answer(f"⚠️ Only {total_qty} igs available in {period_label}.", show_alert=True)
            return

        # Fast answer to stop Telegram button spinner
        await query.answer()

        async with AsyncSessionLocal() as db:
            wallet_balance = await WalletService.get_balance(db, user_id)
            price, currency = await PricingService.get_effective_price(db, day_number=day_num)

        period_label = "1 day old" if day_num == 1 else f"{day_num} days old"
        total_cost = int(round(price * qty))
        can_afford = wallet_balance >= total_cost

        if not can_afford:
            shortfall = total_cost - wallet_balance
            text = (
                "⚠️ Insufficient Wallet Balance\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"# {period_label} Igs\n"
                f"🔢 Selected Quantity: {qty} igs\n"
                f"💰 Price per : ₹{price:,.0f} /- per \n"
                f"💵 Total Cost:  ₹ {total_cost:,.0f}\n"
                f"💳 Balance: ₹{wallet_balance:,.0f}\n"
                f"❌ Shortfall: ₹{shortfall:,.0f}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Please add funds to your wallet to complete this purchase."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund", style="primary")],
                [InlineKeyboardButton(text=f"« Back to {period_label}", callback_data=f"buy_day:{day_num}", style="danger")]
            ])
        else:
            balance_after = float(wallet_balance - total_cost)
            text = (
                "🛒 CONFIRM PURCHASE\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"# {period_label} Igs\n"
                f"🫟 Quantity: {qty} igs\n"
                f"💰 Price per : ₹{price:,.0f} /- per \n"
                f"💵 Total Cost:  ₹ {total_cost:,.0f}\n"
                f"💳 Balance After Purchase:  ₹{balance_after:,.2f}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Click Confirm to complete your purchase of {qty}  Indo Igs:"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ Confirm Purchase (₹{total_cost:,.0f})", callback_data=f"exec_buy:{day_num}:{qty}", style="success")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data=f"buy_day:{day_num}", style="danger")]
            ])

        if query.message:
            try:
                await query.message.edit_text(text, reply_markup=kb)
            except Exception:
                pass


@buy_indo_router.callback_query(F.data.startswith("buy_custom:"))
async def handle_buy_custom_click(query: CallbackQuery, state: FSMContext) -> None:
    """Prompt user to type a custom quantity."""
    user = query.from_user
    user_id = user.id if user else 0
    async with buy_click_guard(user_id, query) as ok:
        if not ok:
            return

        try:
            day_num = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            await query.answer("Invalid category.", show_alert=True)
            return

        await query.answer()
        await state.set_state(BuyIndoState.waiting_for_custom_quantity)
        await state.update_data(day_number=day_num)

        total_qty = StockTxtService.get_stock_count_for_day(day_num)
        period_label = "1 day old" if day_num == 1 else f"{day_num} days old"

        prompt = (
            "✏️ Enter Custom Quantity\n\n"
            f"📦 Category: {period_label}\n"
            f"📊 Available Stock: {total_qty} igs\n\n"
            "Please reply with the exact number of igs you want to buy (e.g., 2, 7, 15):\n"
            "Send /cancel to abort."
        )
        if query.message:
            await query.message.answer(prompt)


@buy_indo_router.message(BuyIndoState.waiting_for_custom_quantity, Command("cancel"))
async def handle_cancel_custom_qty(message: Message, state: FSMContext) -> None:
    """Cancel custom quantity input."""
    data = await state.get_data()
    day_num = data.get("day_number", 1)
    await state.clear()
    period_label = "1 day old" if day_num == 1 else f"{day_num} days old"
    await message.answer(f"❌ Cancelled custom quantity. Returning to {period_label}.", reply_markup=get_buy_quantity_keyboard(day_num))


@buy_indo_router.message(BuyIndoState.waiting_for_custom_quantity)
async def process_custom_quantity_input(message: Message, state: FSMContext) -> None:
    """Validate user entered quantity and show confirmation prompt."""
    user = message.from_user
    user_id = user.id if user else 0
    async with buy_click_guard(user_id) as ok:
        if not ok:
            return

        raw = (message.text or "").strip()
        try:
            qty = int(raw)
            if qty <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            await message.answer("⚠️ Please enter a valid positive number.")
            return

        data = await state.get_data()
        day_num = data.get("day_number", 1)
        period_label = "1 day old" if day_num == 1 else f"{day_num} days old"

        total_qty = StockTxtService.get_stock_count_for_day(day_num)
        if qty > total_qty:
            await message.answer(f"⚠️ Only {total_qty} igs available in {period_label}. Please enter {total_qty} or less.")
            return

        await state.clear()

        async with AsyncSessionLocal() as db:
            wallet_balance = await WalletService.get_balance(db, user_id)
            price, currency = await PricingService.get_effective_price(db, day_number=day_num)

        total_cost = int(round(price * qty))
        can_afford = wallet_balance >= total_cost

        if not can_afford:
            shortfall = total_cost - wallet_balance
            text = (
                "⚠️ Insufficient Wallet Balance\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"# {period_label} Igs\n"
                f"🔢 Selected Quantity: {qty} igs\n"
                f"💰 Price per : ₹{price:,.0f} /- per \n"
                f"💵 Total Cost:  ₹ {total_cost:,.0f}\n"
                f"💳 Balance: ₹{wallet_balance:,.0f}\n"
                f"❌ Shortfall: ₹{shortfall:,.0f}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "Please add funds to your wallet to complete this purchase."
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund", style="primary")],
                [InlineKeyboardButton(text=f"« Back to {period_label}", callback_data=f"buy_day:{day_num}", style="danger")]
            ])
        else:
            balance_after = float(wallet_balance - total_cost)
            text = (
                "🛒 CONFIRM PURCHASE\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"# {period_label} Igs\n"
                f"🫟 Quantity: {qty} igs\n"
                f"💰 Price per : ₹{price:,.0f} /- per \n"
                f"💵 Total Cost:  ₹ {total_cost:,.0f}\n"
                f"💳 Balance After Purchase:  ₹{balance_after:,.2f}\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                f"Click Confirm to complete your purchase of {qty}  Indo Igs:"
            )
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"✅ Confirm Purchase (₹{total_cost:,.0f})", callback_data=f"exec_buy:{day_num}:{qty}", style="success")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data=f"buy_day:{day_num}", style="danger")]
            ])

        await message.answer(text, reply_markup=kb)


@buy_indo_router.callback_query(F.data.startswith("exec_buy:"))
async def handle_execute_batch_buy(query: CallbackQuery) -> None:
    """Execute batch purchase: atomically deduct from stock.txt, deduct wallet, append to sold.txt."""
    user = query.from_user
    if not user:
        await query.answer("User context missing.", show_alert=True)
        return

    async with buy_click_guard(user.id, query) as ok:
        if not ok:
            return

        parts = query.data.split(":")
        if len(parts) != 3:
            await query.answer("Invalid purchase parameters.", show_alert=True)
            return

        try:
            day_num = int(parts[1])
            qty = int(parts[2])
        except ValueError:
            await query.answer("Malformed parameters.", show_alert=True)
            return

        period_label = "1 day old" if day_num == 1 else f"{day_num} days old"

        # Immediately disable button in UI to prevent subsequent clicks
        if query.message:
            try:
                await query.message.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⏳ Processing purchase...", callback_data="noop")]
                    ])
                )
            except Exception:
                pass

        # 1. Price check and wallet verification
        async with AsyncSessionLocal() as db:
            price, currency = await PricingService.get_effective_price(db, day_number=day_num)
            wallet_balance = await WalletService.get_balance(db, user.id)

        total_cost = int(round(price * qty))
        if wallet_balance < total_cost:
            await query.answer("❌ Insufficient wallet balance.", show_alert=True)
            if query.message:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund", style="primary")],
                    [InlineKeyboardButton(text=f"« Back to {period_label}", callback_data=f"buy_day:{day_num}", style="danger")]
                ])
                try:
                    await query.message.edit_text("❌ Insufficient wallet balance.", reply_markup=kb)
                except Exception:
                    pass
            return

        order_code = f"ORD-{secrets.randbelow(900000) + 100000}"

        # 2. Atomically purchase accounts from stock.txt (removes from stock.txt, appends to sold.txt)
        txt_ok, txt_msg, purchased_accounts = StockTxtService.purchase_accounts(
            day_number=day_num,
            quantity=qty
        )

        if not txt_ok or not purchased_accounts:
            await query.answer(f"❌ Purchase failed: {txt_msg}", show_alert=True)
            if query.message:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"« Back to {period_label}", callback_data=f"buy_day:{day_num}", style="danger")]
                ])
                try:
                    await query.message.edit_text(f"❌ Purchase failed: {txt_msg}", reply_markup=kb)
                except Exception:
                    pass
            return

        # 3. Deduct wallet balance in SQL database and create Order
        async with AsyncSessionLocal() as db:
            deduct_ok, deduct_msg, remaining_bal = await WalletService.deduct_for_purchase(
                db=db,
                telegram_user_id=user.id,
                amount=total_cost,
                order_code=order_code,
                item_title=f"{period_label} ({qty} igs)"
            )
            if not deduct_ok:
                # If wallet deduction failed, put accounts back into stock.txt
                raw_lines = "\n".join(acc["raw_line"] for acc in purchased_accounts)
                StockTxtService.add_stock_lines(raw_lines)
                await query.answer(f"❌ Payment failed: {deduct_msg}", show_alert=True)
                return

            now_utc = datetime.now(timezone.utc)
            order = Order(
                order_code=order_code,
                telegram_user_id=user.id,
                telegram_username=user.username,
                telegram_first_name=user.first_name,
                status="COMPLETED",
                total_amount=float(total_cost),
                currency=currency,
                payment_method="WALLET",
                tx_reference=f"WALLET_DEBIT_{order_code}",
                fulfillment_released=True,
                created_at=now_utc
            )
            db.add(order)
            await db.flush()

            for acc in purchased_accounts:
                oi = OrderItem(
                    order_id=order.id,
                    inventory_item_id=None,
                    item_title=f"Indo - {acc.get('username') or acc['email']}",
                    item_sku=acc["email"],
                    unit_price=float(price),
                    quantity=1,
                    subtotal=float(price),
                    day_group=period_label,
                    released_payload=acc["raw_line"]
                )
                db.add(oi)

            await db.commit()

        await query.answer("✅ Purchase successful!", show_alert=False)

        # 4. Remaining stock for this day
        remaining_stock = StockTxtService.get_stock_count_for_day(day_num)
        rem_txt, rem_count = StockTxtService.get_remaining_stock_txt_content(day_number=day_num)

        # 5. Format client delivery response
        delivery_text = format_delivery_message(
            accounts=purchased_accounts,
            day_number=day_num,
            remaining_stock=remaining_stock
        )

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🛒 Buy More", callback_data=f"buy_day:{day_num}", style="primary"),
                InlineKeyboardButton(text="📦 Total Stock", callback_data="refresh_total_stock", style="primary")
            ],
            [
                InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")
            ]
        ])

        if query.message:
            try:
                await query.message.edit_text(delivery_text, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.error(f"Failed to edit message with delivery text: {e}")
                await query.message.answer(delivery_text, parse_mode="HTML", reply_markup=kb)

        # 6. Notify Administrator with details and attach remaining stock in .txt format
        now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
        cust_tag = f"@{user.username}" if user.username else (user.first_name or f"User {user.id}")

        admin_lines = [
            "🔔 <b>NEW PURCHASE NOTIFICATION</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"👤 <b>Customer:</b> {html.escape(cust_tag)} (<code>{user.id}</code>)",
            f"📦 <b>Category:</b> {period_label}",
            f"🔢 <b>Total Igs:</b> <code>{qty} igs</code>",
            f"💰 <b>Total Amount:</b> <code>₹{total_cost:,.2f}</code>",
            f"🧾 <b>Order Code:</b> <code>{order_code}</code>",
            f"🕒 <b>Time:</b> <code>{now_str}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📧 <b>Purchased Accounts ({len(purchased_accounts)}):</b>",
        ]
        for idx, acc in enumerate(purchased_accounts, start=1):
            admin_lines.append(f"{idx}. <code>{html.escape(acc['email'])}</code> | Pass: <code>{html.escape(acc['password'])}</code>")

        admin_caption = "\n".join(admin_lines)

        admin_ids = settings.admin_ids
        for admin_chat_id in admin_ids:
            try:
                if rem_txt:
                    rem_doc = BufferedInputFile(
                        rem_txt.encode("utf-8"),
                        filename=f"remaining_stock_day_{day_num}.txt"
                    )
                    await query.bot.send_document(
                        chat_id=admin_chat_id,
                        document=rem_doc,
                        caption=admin_caption,
                        parse_mode="HTML"
                    )
                else:
                    await query.bot.send_message(
                        chat_id=admin_chat_id,
                        text=admin_caption + "\n\nℹ️ <i>No remaining stock left in this category.</i>",
                        parse_mode="HTML"
                    )
            except Exception as err:
                logger.error(f"Failed to notify admin {admin_chat_id}: {err}")


@buy_indo_router.callback_query(F.data.startswith("confirm_buy:"))
async def handle_confirm_buy(query: CallbackQuery) -> None:
    """
    Backward-compatible single unit buy handler.
    Delegates to batch purchase with qty=1.
    """
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer("Invalid purchase request.", show_alert=True)
        return

    try:
        expected_day = int(parts[2])
    except ValueError:
        await query.answer("Malformed purchase parameters.", show_alert=True)
        return

    query.data = f"exec_buy:{expected_day}:1"
    await handle_execute_batch_buy(query)


@buy_indo_router.callback_query(F.data.startswith("cancel_buy:"))
async def handle_cancel_purchase_callback(query: CallbackQuery, state: FSMContext | None = None) -> None:
    """Handle customer cancelling purchase confirmation."""
    user = query.from_user
    user_id = user.id if user else 0
    async with buy_click_guard(user_id, query) as ok:
        if not ok:
            return
        if state:
            await state.clear()
        try:
            day_num = int(query.data.split(":")[1])
        except (IndexError, ValueError):
            day_num = 1
        query.data = f"buy_day:{day_num}"
        await handle_select_age_category(query, state)
