"""
Admin Price Management Telegram Flow.
Allows authorized administrator to:
- Set global Indo Igs price across all days (Day 1 to infinity) with instant effect.
- Set and change individual category prices from Day 1 Old to infinity.
- View all category prices in a live summary.
- Review and confirm price adjustments before applying.
- Prevent negative prices and enforce zero-price authorization.
- Quick commands: /setprice <amount>, /setprice <day> <amount>, /prices.
- Enforce server-side authorization on every handler.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.services.pricing_service import PricingService
from indo_selling_bot.config import settings
from indo_selling_bot.bot.keyboards import (
    get_admin_main_keyboard,
    get_admin_pricing_keyboard,
    get_admin_price_category_selector_keyboard,
    get_price_confirmation_keyboard,
)

admin_pricing_router = Router()

class AdminPricingState(StatesGroup):
    waiting_for_global_price_input = State()
    waiting_for_day_selection = State()
    waiting_for_price_input = State()
    waiting_for_confirmation = State()

def _is_admin(user_id: int) -> bool:
    """Strict administrator check."""
    return settings.is_admin(user_id)

# ==========================================
# 1. Price Management Submenu Entry
# ==========================================
@admin_pricing_router.message(F.text.in_(["💵 Price Management", "💵 Manage Prices"]))
@admin_pricing_router.callback_query(F.data.in_(["admin:manage_prices", "admin:pricing"]))
async def handle_pricing_menu(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Open the Price Management administrative panel."""
    await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Unauthorized: Admin access required.", show_alert=True)
        else:
            await event.answer("⛔ Unauthorized: Admin access required.")
        return

    async with AsyncSessionLocal() as db:
        global_price = await PricingService.get_global_price(db)

    text = (
        "💵 *PRICE MANAGEMENT DASHBOARD*\n\n"
        f"💰 *Current Global Price:* `₹{global_price:,.0f} /- per` (All Days 1 to ∞)\n\n"
        "Configure and update category prices for Indo Igs:\n"
        "• *Set Price for All Days:* Instantly updates price for all days (1 to ∞).\n"
        "• *Set Specific Day Price:* Override price for individual age categories.\n"
        "• *Quick Commands:*\n"
        "  `/setprice 7` — Set all days to ₹7\n"
        "  `/setprice 10 9` — Set Day 10 specifically to ₹9\n"
        "  `/prices` — View live category prices\n\n"
        "Select an action below:"
    )

    kb = get_admin_pricing_keyboard()
    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


# ==========================================
# 2. View All Prices
# ==========================================
@admin_pricing_router.message(F.text == "📋 View All Prices")
@admin_pricing_router.message(Command("prices"))
@admin_pricing_router.message(Command("pricing"))
@admin_pricing_router.callback_query(F.data == "admin:price:view_all")
async def handle_view_all_prices(event: Message | CallbackQuery, state: FSMContext | None = None) -> None:
    """Display live price summary across categories."""
    if state:
        await state.clear()
    user_id = event.from_user.id if event.from_user else 0
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Unauthorized.", show_alert=True)
        else:
            await event.answer("⛔ Unauthorized.")
        return

    async with AsyncSessionLocal() as db:
        global_price = await PricingService.get_global_price(db)
        all_prices = await PricingService.get_all_category_prices(db, max_days=20)

    lines = [
        "📋 *LIVE CATEGORY PRICE SUMMARY*\n",
        f"🌐 *Global Default Price:* `₹{global_price:,.0f} /- per` (Days 1 to ∞)\n",
        "Day Category | Unit Price | Status",
        "━━━━━━━━━━━━━━━━━━━━━━"
    ]

    for item in all_prices:
        day = item["day"]
        price = item["price"]
        cur = item["currency"]
        custom_mark = "✏️" if item["is_custom"] else "⚪"
        lines.append(f"{custom_mark} *Day {day} Old* — `₹{price:,.0f}`")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 *Note:* ✏️ = Custom price | ⚪ = Global default price.")
    lines.append("Past orders preserve historical price charged at checkout.")

    text = "\n".join(lines)
    kb = get_admin_pricing_keyboard()

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


# ==========================================
# 3. Global Price Setting (All Days 1 to ∞)
# ==========================================
@admin_pricing_router.callback_query(F.data == "admin:price:set_global")
async def handle_set_global_price_click(query: CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to enter new price for all days."""
    user = query.from_user
    if not user or not _is_admin(user.id):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        current_price = await PricingService.get_global_price(db)

    await state.set_state(AdminPricingState.waiting_for_global_price_input)
    text = (
        "🌐 *SET GLOBAL PRICE FOR ALL INDO IGS*\n\n"
        f"• *Current Default Price:* `₹{current_price:,.0f} /- per`\n\n"
        "Please reply with the *new unit price in ₹* for all Indo Igs (Days 1 to ∞).\n"
        "Examples: `7`, `8`, `10`\n\n"
        "_Send /cancel to abort._"
    )
    await query.answer()
    if query.message:
        await query.message.edit_text(text, parse_mode="Markdown")


@admin_pricing_router.message(AdminPricingState.waiting_for_global_price_input)
async def handle_global_price_input(message: Message, state: FSMContext) -> None:
    """Process new global price input from admin."""
    user = message.from_user
    if not user or not _is_admin(user.id):
        await message.answer("⛔ Unauthorized.")
        await state.clear()
        return

    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "cancel", "« back", "back"):
        await state.clear()
        await message.answer("❌ Price update cancelled.", reply_markup=get_admin_pricing_keyboard())
        return

    clean_text = text.replace("₹", "").replace("INR", "").replace("Rs", "").replace("rs", "").strip()
    try:
        new_price = float(clean_text)
    except ValueError:
        await message.answer("❌ Invalid number. Please enter a valid price (e.g. `7` or `10`).")
        return

    if new_price <= 0:
        await message.answer("❌ Price must be greater than 0.")
        return

    async with AsyncSessionLocal() as db:
        success, msg = await PricingService.set_global_price(db, new_price, user.id)

    await state.clear()
    if not success:
        await message.answer(f"❌ Failed: {msg}", reply_markup=get_admin_pricing_keyboard())
        return

    final_text = (
        "✅ *GLOBAL INDO PRICE UPDATED!*\n\n"
        f"💰 *New Active Price:* `₹{new_price:,.0f} /- per` across all days (Day 1 to ∞)\n"
        f"👤 *Updated By Admin:* `{user.id}`\n\n"
        f"All customer checkouts and category displays will now reflect ₹{new_price:,.0f} per account."
    )
    await message.answer(final_text, parse_mode="Markdown", reply_markup=get_admin_pricing_keyboard())


# ==========================================
# 4. Quick Command /setprice
# ==========================================
@admin_pricing_router.message(Command("setprice"))
async def handle_setprice_command(message: Message) -> None:
    """Quick command to set price: /setprice <amount> or /setprice <day> <amount>."""
    user = message.from_user
    if not user or not _is_admin(user.id):
        await message.answer("⛔ Unauthorized: Admin access required.")
        return

    parts = (message.text or "").split()[1:]
    if not parts:
        async with AsyncSessionLocal() as db:
            cur = await PricingService.get_global_price(db)
        await message.answer(
            "💵 *INDO PRICE MANAGEMENT*\n\n"
            f"• *Current Global Price:* `₹{cur:,.0f} /- per` (all days)\n\n"
            "*Usage:*\n"
            "• `/setprice 7` — Set price to ₹7 for all days (1 to ∞)\n"
            "• `/setprice 10 9` — Set Day 10 price specifically to ₹9\n"
            "• `/prices` — View all category prices",
            parse_mode="Markdown"
        )
        return

    if len(parts) == 1:
        clean_text = parts[0].replace("₹", "").replace("INR", "").replace("Rs", "").replace("rs", "").strip()
        try:
            val = float(clean_text)
        except ValueError:
            await message.answer("❌ Invalid price number. Usage: `/setprice 7`", parse_mode="Markdown")
            return

        if val <= 0:
            await message.answer("❌ Price must be greater than 0.")
            return

        async with AsyncSessionLocal() as db:
            success, msg = await PricingService.set_global_price(db, val, user.id)

        if success:
            await message.answer(
                f"✅ *Price Updated!*\n\nAll Indo Igs (Days 1 to ∞) are now set to `₹{val:,.0f} /- per`.",
                parse_mode="Markdown",
                reply_markup=get_admin_pricing_keyboard()
            )
        else:
            await message.answer(f"❌ Failed: {msg}")

    elif len(parts) >= 2:
        try:
            day = int(parts[0])
            val = float(parts[1].replace("₹", "").replace("INR", "").replace("Rs", "").replace("rs", "").strip())
        except ValueError:
            await message.answer("❌ Invalid parameters. Usage: `/setprice <day> <amount>` (e.g. `/setprice 10 7`)", parse_mode="Markdown")
            return

        if day < 1:
            await message.answer("❌ Day must be 1 or higher.")
            return
        if val <= 0:
            await message.answer("❌ Price must be greater than 0.")
            return

        async with AsyncSessionLocal() as db:
            success, msg, cp = await PricingService.set_category_price(
                db=db,
                day_number=day,
                new_price=val,
                admin_id=user.id,
                reason=f"Admin {user.id} updated Day {day} via /setprice command"
            )

        if success:
            await message.answer(
                f"✅ *Category Price Updated!*\n\nDay {day} Old Indo Igs is now set to `₹{val:,.0f} /- per`.",
                parse_mode="Markdown",
                reply_markup=get_admin_pricing_keyboard()
            )
        else:
            await message.answer(f"❌ Failed: {msg}")


# ==========================================
# 5. Select Category to Set / Change Price
# ==========================================
@admin_pricing_router.message(F.text.in_(["💵 Set Category Price", "✏️ Change Price"]))
@admin_pricing_router.callback_query(F.data.in_(["admin:price:set", "admin:price:change"]))
async def handle_select_price_category(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Prompt admin to select which day category to update."""
    user_id = event.from_user.id if event.from_user else 0
    if not _is_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Unauthorized.", show_alert=True)
        else:
            await event.answer("⛔ Unauthorized.")
        return

    async with AsyncSessionLocal() as db:
        categories_pricing = await PricingService.get_all_category_prices(db, max_days=20)

    text = (
        "✏️ *SELECT AGE CATEGORY TO CONFIGURE*\n\n"
        "Tap a day category below to adjust its unit price:\n"
        "_(Current price shown in parentheses)_"
    )

    kb = get_admin_price_category_selector_keyboard(categories_pricing, action_prefix="price_sel")
    await state.set_state(AdminPricingState.waiting_for_day_selection)

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@admin_pricing_router.callback_query(F.data.startswith("price_sel:"))
async def handle_category_selected(query: CallbackQuery, state: FSMContext) -> None:
    """Admin selected a day category, prompt for new price."""
    user = query.from_user
    if not user or not _is_admin(user.id):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    try:
        day_num = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid selection.", show_alert=True)
        return

    if day_num < 1:
        await query.answer("Category day must be 1 or higher.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        current_price, currency = await PricingService.get_effective_price(db, day_num)

    await state.update_data(target_day=day_num, current_price=current_price, currency="INR")
    await state.set_state(AdminPricingState.waiting_for_price_input)

    text = (
        f"💵 *SET PRICE FOR DAY {day_num} OLD*\n\n"
        f"• *Current Price:* `₹{current_price:,.0f} /- per`\n\n"
        "Please send the *new unit price* as a number.\n"
        "Examples: `7`, `10`, `15`\n\n"
        "_(To offer as a free promotion, enter `0 free`)_\n"
        "Send /cancel to abort."
    )

    await query.answer()
    if query.message:
        await query.message.edit_text(text, parse_mode="Markdown")


# ==========================================
# 6. Input Price Validation & Review Screen
# ==========================================
@admin_pricing_router.message(AdminPricingState.waiting_for_price_input)
async def handle_price_input(message: Message, state: FSMContext) -> None:
    """Validate user input price, enforce non-negative rules, and render confirmation review."""
    user = message.from_user
    if not user or not _is_admin(user.id):
        await message.answer("⛔ Unauthorized.")
        await state.clear()
        return

    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "cancel", "« back", "back"):
        await state.clear()
        await message.answer("❌ Price update cancelled.", reply_markup=get_admin_pricing_keyboard())
        return

    data = await state.get_data()
    day_num = data.get("target_day", 1)
    current_price = data.get("current_price", 7.0)
    currency = "INR"

    allow_free = False
    clean_text = text.replace(",", "").replace(" ", "").upper()

    if "FREE" in clean_text:
        allow_free = True
        clean_text = clean_text.replace("FREE", "").replace("0", "0")
        if clean_text == "" or clean_text == "0":
            parsed_price = 0.0
        else:
            try:
                parsed_price = float(clean_text)
            except ValueError:
                await message.answer("❌ Invalid format. To make free, reply `0 free`. Otherwise enter numbers only.")
                return
    else:
        clean_text = clean_text.replace("INR", "").replace("₹", "").replace("RS", "").replace("$", "")
        try:
            parsed_price = float(clean_text)
        except ValueError:
            await message.answer("❌ Invalid input. Please enter a valid number (e.g., `7` or `10`).")
            return

    if parsed_price < 0:
        await message.answer("❌ Price cannot be negative. Please enter a valid positive number.")
        return

    if parsed_price == 0 and not allow_free:
        await message.answer(
            "⚠️ Zero price detected.\n"
            "Free items must be explicitly authorized. To set price to 0, please send `0 free`."
        )
        return

    if parsed_price > 100_000_000:
        await message.answer("❌ Price exceeds maximum allowable limit (100,000,000). Please check your input.")
        return

    await state.update_data(pending_price=parsed_price, allow_free=allow_free)
    await state.set_state(AdminPricingState.waiting_for_confirmation)

    review_text = (
        "📋 *PRICE CHANGE REVIEW*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *Category:* `Day {day_num} Old`\n"
        f"💰 *Previous Price:* `₹{current_price:,.0f}`\n"
        f"✨ *New Unit Price:* `₹{parsed_price:,.0f}`\n"
        f"🆓 *Free Item Allowed:* `{'Yes' if allow_free else 'No'}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Ready to apply this price to future customer purchases?\n"
        "_(Past orders and receipts will not be modified)_"
    )

    kb = get_price_confirmation_keyboard(
        day_number=day_num,
        new_price=parsed_price,
        allow_free=allow_free
    )
    await message.answer(review_text, parse_mode="Markdown", reply_markup=kb)


# ==========================================
# 7. Confirm and Apply Price
# ==========================================
@admin_pricing_router.callback_query(F.data.startswith("price_confirm:"))
async def handle_confirm_price(query: CallbackQuery, state: FSMContext) -> None:
    """Execute the category price update transactionally with audit logging."""
    user = query.from_user
    if not user or not _is_admin(user.id):
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    parts = query.data.split(":")
    if len(parts) < 4:
        await query.answer("Malformed confirmation parameters.", show_alert=True)
        return

    try:
        day_num = int(parts[1])
        new_price = float(parts[2])
        allow_free = bool(int(parts[3]))
    except (ValueError, IndexError):
        await query.answer("Invalid confirmation parameters.", show_alert=True)
        return

    async with AsyncSessionLocal() as db:
        success, message, cp = await PricingService.set_category_price(
            db=db,
            day_number=day_num,
            new_price=new_price,
            admin_id=user.id,
            allow_free=allow_free,
            reason=f"Admin {user.id} updated Day {day_num} price via Telegram Panel"
        )

    await state.clear()
    if not success:
        await query.answer(f"❌ Failed: {message}", show_alert=True)
        return

    await query.answer("✅ Price updated successfully!", show_alert=True)

    cur = cp.currency if cp else "INR"
    final_text = (
        "✅ *CATEGORY PRICE UPDATED!*\n\n"
        f"📦 *Category:* `Day {day_num} Old`\n"
        f"💵 *Active Unit Price:* `₹{new_price:,.0f} /- per`\n"
        f"👤 *Updated By Admin:* `{user.id}`\n"
        f"🕒 *Timestamp:* `Just now (UTC)`\n\n"
        "Customers viewing this category will immediately see the updated price at checkout."
    )

    kb = get_admin_pricing_keyboard()
    if query.message:
        await query.message.edit_text(final_text, parse_mode="Markdown", reply_markup=kb)
