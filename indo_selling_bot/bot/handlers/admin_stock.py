"""
Administrative Stock Management and Verification Handlers for aiogram 3 Telegram Bot.
Includes:
- Strict server-side authorization checking (TELEGRAM_ADMIN_USER_ID)
- Guided FSM for batch stock addition with Day 1..20 category selection, quantity, and timezone-aware timestamps
- Administrative stock inspection with internal batch details
- Deposit review, atomic wallet crediting, and idempotent approval/rejection
- Dynamic business UPI and QR configuration management
"""
import logging
import html
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, desc

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.import_history import StockImportHistory
from indo_selling_bot.models.order import Order
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.stock_txt_service import StockTxtService
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.payment_setting_service import PaymentSettingService
from indo_selling_bot.services.stock_import_service import StockImportService, ImportPreview, MAX_IMPORT_FILE_SIZE_BYTES
from indo_selling_bot.services.delivery_service import DeliveryService
from indo_selling_bot.bot.keyboards import (
    get_admin_main_keyboard,
    get_admin_day_selection_keyboard,
    get_admin_remove_day_keyboard,
    get_admin_upi_settings_keyboard,
    get_deposit_action_keyboard,
    get_main_reply_keyboard,
    get_import_preview_keyboard,
    get_import_errors_keyboard,
)

logger = logging.getLogger("admin_stock")

admin_stock_router = Router()

# ==========================================
# FSM States
# ==========================================
class AdminAddStockState(StatesGroup):
    waiting_for_manual_text = State()
    waiting_for_day = State()
    waiting_for_quantity = State()
    waiting_for_created_at = State()
    waiting_for_price = State()
    waiting_for_title = State()
    waiting_for_confirm = State()

class AdminRemoveStockState(StatesGroup):
    waiting_for_day = State()
    waiting_for_quantity = State()
    waiting_for_confirm = State()

class AdminUpiSettingsState(StatesGroup):
    waiting_for_upi_id = State()
    waiting_for_payee_name = State()
    waiting_for_instructions = State()
    waiting_for_qr = State()

class AdminImportTxtState(StatesGroup):
    waiting_for_file = State()


# ==========================================
# Authorization Helpers
# ==========================================
def check_admin(user_id: int | None) -> bool:
    if not user_id:
        return False
    return settings.is_admin(user_id)


# ==========================================
# Admin Entry Point & Dashboard
# ==========================================
@admin_stock_router.message(F.text.in_(["🛡️ Admin Panel", "🔐 Admin / Add Stock", "🔐 Admin Panel"]))
@admin_stock_router.message(Command("admin"))
async def handle_admin_menu(message: Message, state: FSMContext) -> None:
    """Entry point for administrator menu with strict server-side authorization check."""
    await state.clear()
    user = message.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await message.answer(
            "⛔ *Unauthorized Access*\n\n"
            "This area is restricted to configured system administrators only.\n"
            "Your Telegram user ID is not registered with administrator privileges.",
            parse_mode="Markdown"
        )
        return

    text = (
        "🔐 *Administrator Control Panel*\n\n"
        f"Authenticated as Admin ID: `{user_id}`\n"
        f"Configured Business Timezone: `{settings.BUSINESS_TIMEZONE}`\n\n"
        "Select an administrative operation:"
    )
    await message.answer(
        text,
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


@admin_stock_router.callback_query(F.data.in_(["admin:main", "admin:back_main"]))
async def handle_admin_back_to_menu(query: CallbackQuery, state: FSMContext) -> None:
    """Return to admin main menu dashboard."""
    await state.clear()
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()
    text = (
        "🔐 *Administrator Control Panel*\n\n"
        f"Authenticated as Admin ID: `{user_id}`\n"
        f"Configured Business Timezone: `{settings.BUSINESS_TIMEZONE}`\n\n"
        "Select an administrative operation:"
    )
    if query.message:
        await query.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_admin_main_keyboard()
        )


# ==========================================
# View Stock (Admin Details)
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:view_stock")
async def handle_admin_view_stock(query: CallbackQuery) -> None:
    """View detailed inventory with SKU codes and creation timestamps."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()
    summary = StockTxtService.get_total_stock_summary(max_days=20)

    lines = [
        "📊 *Admin Stock Inspection (Day 1 - Day 20)*",
        f"🌐 Timezone: `{settings.BUSINESS_TIMEZONE}`\n"
    ]

    for cat in summary["categories"]:
        day_num = cat["day"]
        qty = cat["quantity"]
        label = cat["label"]
        if qty > 0:
            lines.append(f"🟢 *{label}:* `{qty} unit(s)`")
            for b in cat["batches"]:
                created_str = b["created_at"].strftime("%Y-%m-%d %H:%M UTC")
                lines.append(f"   • `ID:{b['id']}` | SKU: `{b['sku']}` | `{b['quantity']} pcs` | Created: {created_str}")
        else:
            lines.append(f"⚪ *{label}:* `0 units`")

    lines.append("\n" + "━" * 26)
    lines.append(f"📈 *Total Available Stock:* `{summary['total_stock']} unit(s)`")
    if summary["older_stock_count"] > 0:
        lines.append(f"🏷️ *Mature Batches (>20 Days):* `{summary['older_stock_count']} unit(s)`")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:view_stock")],
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
    ])

    if query.message:
        await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


# ==========================================
# Manual Entry & Guided FSM: Add Stock
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:add_stock")
@admin_stock_router.message(Command("add_stock"))
async def handle_admin_add_stock_start(event: CallbackQuery | Message, state: FSMContext) -> None:
    """Start manual stock addition flow with quick paste option or guided wizard."""
    user = event.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        else:
            await event.answer("⛔ Unauthorized: Administrator access required.")
        return

    await state.clear()
    await state.set_state(AdminAddStockState.waiting_for_manual_text)

    prompt = (
        "📦 *Add Stock Manually*\n\n"
        "Paste your account line(s) directly into this chat:\n\n"
        "📋 *Supported Format:*\n"
        "`mail:pass:username:date:time`\n\n"
        "_Example:_\n"
        "`( neha6501@metamail.store:654654@@:neha404714:11 Oct 2026, 01:14:43 IST )`\n\n"
        "• Parentheses `(...)` and quotes are optional.\n"
        "• You can paste a single account or multiple lines at once.\n"
        "• Age category (Day 1 - Day 20 Old) is calculated dynamically from the timestamp.\n"
        "• Duplicate checking and timestamp verification are performed automatically.\n"
        "• You can also send `.txt` files or pipe format (`ITEM_ID|CREATED_AT`).\n\n"
        "_Tap below if you prefer the step-by-step batch wizard._"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔢 Step-by-Step Batch Wizard", callback_data="admin:add_stock_guided")],
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
    ])

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(prompt, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.answer(prompt, parse_mode="Markdown", reply_markup=kb)


@admin_stock_router.callback_query(F.data == "admin:add_stock_guided")
async def handle_admin_add_stock_guided(query: CallbackQuery, state: FSMContext) -> None:
    """Start guided stock addition flow: Day category selection."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()
    await state.clear()
    await state.set_state(AdminAddStockState.waiting_for_day)

    text = (
        "📦 *Add Stock Batch - Step 1 of 5*\n\n"
        "Select the target age category for this inventory batch (Day 1 to Day 20):\n"
        "_Batches are dynamically aged 24h from their creation timestamp._"
    )
    if query.message:
        await query.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_admin_day_selection_keyboard(max_days=20)
        )


@admin_stock_router.message(AdminAddStockState.waiting_for_manual_text, F.text)
async def handle_admin_manual_stock_text(message: Message, state: FSMContext) -> None:
    """Parse pasted text lines (mail:pass:username:date:time or pipe format) and display preview."""
    user = message.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer("❌ Operation cancelled.", reply_markup=get_admin_main_keyboard())
        return

    status_msg = await message.answer("⏳ Validating and parsing stock entries...")

    async with AsyncSessionLocal() as db:
        preview = await StockImportService.parse_txt_file(
            content_bytes=raw_text.encode("utf-8"),
            filename="manual_entry.txt",
            db=db
        )

    await state.clear()
    preview_text = format_preview_text(preview)
    has_issues = (preview.total_duplicates > 0 or preview.total_invalid > 0 or preview.total_review > 0)
    keyboard = get_import_preview_keyboard(preview.session_id, has_issues=has_issues)

    try:
        await status_msg.edit_text(preview_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await message.answer(preview_text, parse_mode="HTML", reply_markup=keyboard)


@admin_stock_router.message(AdminAddStockState.waiting_for_manual_text, F.document)
async def handle_admin_manual_stock_document(message: Message, state: FSMContext) -> None:
    """Handle document upload if sent during manual stock entry."""
    await handle_import_txt_file(message, state)


@admin_stock_router.callback_query(AdminAddStockState.waiting_for_day, F.data.startswith("adm_day:"))
async def handle_admin_stock_day_selected(query: CallbackQuery, state: FSMContext) -> None:
    """Process selected age category day."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    try:
        day_num = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid day selection.", show_alert=True)
        return

    await query.answer()
    await state.update_data(day_number=day_num)
    await state.set_state(AdminAddStockState.waiting_for_quantity)

    prompt = (
        f"📦 *Add Stock Batch - Step 2 of 5*\n\n"
        f"Selected Category: *Day {day_num} ({day_num} Day{'s' if day_num > 1 else ''} Old)*\n\n"
        f"Please enter the *quantity* of inventory units to add (e.g. `10`, `50`, `100`):\n\n"
        f"_Send /cancel to abort at any time._"
    )
    if query.message:
        await query.message.edit_text(prompt, parse_mode="Markdown")


@admin_stock_router.message(Command("cancel"), F.chat.type == "private")
@admin_stock_router.callback_query(F.data == "admin:cancel")
async def handle_admin_cancel(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Cancel any active admin conversation and return to admin menu."""
    await state.clear()
    user = event.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        return

    text = "❌ Operation cancelled. Returned to Administrator Control Panel."
    kb = get_admin_main_keyboard()

    if isinstance(event, CallbackQuery):
        await event.answer("Operation cancelled.")
        if event.message:
            await event.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    else:
        await event.answer(text, parse_mode="Markdown", reply_markup=kb)


@admin_stock_router.message(AdminAddStockState.waiting_for_quantity)
async def handle_admin_stock_quantity_input(message: Message, state: FSMContext) -> None:
    """Validate quantity and prompt for creation timestamp."""
    user = message.from_user
    if not check_admin(user.id if user else 0):
        return

    raw_text = (message.text or "").strip()
    try:
        qty = int(raw_text)
        if qty <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid positive integer for quantity (e.g. `25`):")
        return

    await state.update_data(quantity=qty)
    data = await state.get_data()
    day_num = data.get("day_number", 1)

    await state.set_state(AdminAddStockState.waiting_for_created_at)

    tz_name = settings.BUSINESS_TIMEZONE
    prompt = (
        f"📦 *Add Stock Batch - Step 3 of 5*\n\n"
        f"Quantity: `{qty} units` for *Day {day_num}*\n\n"
        f"Specify the original creation timestamp for authentic ageing:\n"
        f"• Reply `auto` (Recommended): Automatically backdates timestamp to match Day {day_num} ({day_num - 1} × 24h ago).\n"
        f"• Or send timestamp in `{tz_name}`: `YYYY-MM-DD HH:MM` (e.g. `2026-10-18 14:30`)\n\n"
        f"_Send /cancel to abort._"
    )
    await message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminAddStockState.waiting_for_created_at)
async def handle_admin_stock_created_at_input(message: Message, state: FSMContext) -> None:
    """Parse creation timestamp or compute automatically."""
    user = message.from_user
    if not check_admin(user.id if user else 0):
        return

    raw_text = (message.text or "").strip()
    data = await state.get_data()
    day_num = data.get("day_number", 1)
    now_utc = datetime.now(timezone.utc)

    if raw_text.lower() in ("auto", "now", "default"):
        # Legitimate offset: Day 1 = <24h ago (0 days offset, 1 hour ago)
        # Day N = (N-1) days offset + 1 hour
        offset_days = max(0, day_num - 1)
        created_at_dt = now_utc - timedelta(days=offset_days, hours=1)
    else:
        try:
            try:
                b_tz = ZoneInfo(settings.BUSINESS_TIMEZONE)
            except Exception:
                b_tz = timezone.utc

            # Support YYYY-MM-DD HH:MM and YYYY-MM-DD
            if len(raw_text) == 10:
                parsed_naive = datetime.strptime(raw_text, "%Y-%m-%d")
            else:
                parsed_naive = datetime.strptime(raw_text, "%Y-%m-%d %H:%M")

            parsed_aware = parsed_naive.replace(tzinfo=b_tz)
            created_at_dt = parsed_aware.astimezone(timezone.utc)
        except ValueError:
            await message.answer(
                "⚠️ Invalid date format. Please reply with `auto` or use format `YYYY-MM-DD HH:MM` (e.g., `2026-10-18 10:00`):"
            )
            return

    await state.update_data(created_at_iso=created_at_dt.isoformat())
    await state.set_state(AdminAddStockState.waiting_for_price)

    prompt = (
        f"📦 *Add Stock Batch - Step 4 of 5*\n\n"
        f"Creation Time: `{created_at_dt.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
        f"Enter the unit price in INR (e.g. `50` or `100`):"
    )
    await message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminAddStockState.waiting_for_price)
async def handle_admin_stock_price_input(message: Message, state: FSMContext) -> None:
    """Validate price and prompt for optional title/notes."""
    user = message.from_user
    if not check_admin(user.id if user else 0):
        return

    raw_text = (message.text or "").strip().replace(",", "").replace(".", "").replace("Rp", "").strip()
    try:
        price = float(raw_text)
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid positive unit price (e.g. `30000`):")
        return

    await state.update_data(price=price)
    data = await state.get_data()
    day_num = data.get("day_number", 1)
    qty = data.get("quantity", 1)

    await state.set_state(AdminAddStockState.waiting_for_title)

    prompt = (
        f"📦 *Add Stock Batch - Step 5 of 5*\n\n"
        f"Price: `₹{price:,.2f}`\n\n"
        f"Enter a custom label/title for this batch, or send `/skip` to use the default title:\n"
        f"_Default: `Indo Stock Day {day_num} ({qty} pcs)`_"
    )
    await message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminAddStockState.waiting_for_title)
async def handle_admin_stock_title_input(message: Message, state: FSMContext) -> None:
    """Store title, display confirmation preview with Confirm/Cancel buttons."""
    user = message.from_user
    if not check_admin(user.id if user else 0):
        return

    raw_text = (message.text or "").strip()
    data = await state.get_data()
    day_num = data.get("day_number", 1)
    qty = data.get("quantity", 1)
    price = data.get("price", 0.0)
    created_at_iso = data.get("created_at_iso")
    created_at_dt = datetime.fromisoformat(created_at_iso)

    title = raw_text if raw_text.lower() not in ("/skip", "skip", "") else f"Indo Stock Day {day_num} ({qty} pcs)"
    await state.update_data(title=title)
    await state.set_state(AdminAddStockState.waiting_for_confirm)

    preview = (
        "📋 *Confirm Batch Creation*\n\n"
        f"• *Target Category:* Day {day_num} ({day_num} Day{'s' if day_num > 1 else ''} Old)\n"
        f"• *Quantity:* `{qty} units`\n"
        f"• *Unit Price:* `₹{price:,.2f}`\n"
        f"• *Creation Timestamp:* `{created_at_dt.strftime('%Y-%m-%d %H:%M UTC')}`\n"
        f"• *Batch Title:* `{title}`\n\n"
        "Click *Confirm & Save* below to write this batch to the inventory database:"
    )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Confirm & Save Batch", callback_data="admin:confirm_save_stock")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel")]
    ])
    await message.answer(preview, parse_mode="Markdown", reply_markup=keyboard)


@admin_stock_router.callback_query(AdminAddStockState.waiting_for_confirm, F.data == "admin:confirm_save_stock")
async def handle_admin_confirm_save_stock(query: CallbackQuery, state: FSMContext) -> None:
    """Persist the inventory batch transactionally to SQLite database with audit record."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    data = await state.get_data()
    day_num = data.get("day_number", 1)
    qty = data.get("quantity", 1)
    price = data.get("price", 0.0)
    title = data.get("title", f"Indo Stock Day {day_num}")
    created_at_dt = datetime.fromisoformat(data["created_at_iso"])

    await state.clear()
    await query.answer("Saving batch...", show_alert=False)

    async with AsyncSessionLocal() as db:
        # Determine category ID: fallback to first category in DB
        cat_stmt = select(Category.id).order_by(Category.id).limit(1)
        cat_id = (await db.execute(cat_stmt)).scalar_one_or_none() or 1

        new_item = await InventoryService.add_stock_batch(
            db=db,
            category_id=cat_id,
            quantity=qty,
            price=price,
            created_at=created_at_dt,
            admin_id=user_id,
            title=title,
            notes=f"Added via Telegram Admin Bot by User {user_id}",
            currency="INR"
        )

    success_text = (
        "✅ *Inventory Batch Added Successfully!*\n\n"
        f"🏷️ *SKU:* `{new_item.sku}`\n"
        f"📦 *Title:* `{new_item.title}`\n"
        f"📊 *Quantity:* `{new_item.quantity} units`\n"
        f"💰 *Unit Price:* `₹{new_item.price:,.2f}`\n"
        f"📅 *Age Category:* `{new_item.get_age_category_label()}`\n"
        f"🕒 *Creation Timestamp:* `{new_item.created_at.strftime('%Y-%m-%d %H:%M UTC')}`\n\n"
        "Customers can immediately browse and purchase this stock via *🛒 Buy Indo*."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Add Another Batch", callback_data="admin:add_stock")],
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
    ])

    if query.message:
        await query.message.edit_text(success_text, parse_mode="Markdown", reply_markup=kb)


# ==========================================
# Remove Stock Flow
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:remove_stock")
async def handle_admin_remove_stock_start(query: CallbackQuery, state: FSMContext) -> None:
    """Entry point for stock removal: select age category."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()
    await state.clear()
    await state.set_state(AdminRemoveStockState.waiting_for_day)

    summary = StockTxtService.get_total_stock_summary(max_days=20)

    text = (
        "➖ *Remove Stock — Select Category*\n\n"
        "Choose an age category to remove inventory units from:\n"
        "_Only active, unsold stock will be removed from stock.txt._"
    )
    if query.message:
        await query.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_admin_remove_day_keyboard(summary["categories"])
        )


@admin_stock_router.callback_query(AdminRemoveStockState.waiting_for_day, F.data.startswith("rem_day:"))
async def handle_admin_remove_day_selected(query: CallbackQuery, state: FSMContext) -> None:
    """Handle category selection for removal."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    try:
        day_num = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        await query.answer("Invalid day selection.", show_alert=True)
        return

    available = StockTxtService.get_stock_count_for_day(day_num)

    if available <= 0:
        await query.answer(f"⚠️ Day {day_num} Old currently has 0 available units to remove.", show_alert=True)
        return

    await query.answer()
    await state.update_data(day_number=day_num, available_qty=available)
    await state.set_state(AdminRemoveStockState.waiting_for_quantity)

    prompt = (
        f"➖ *Remove Stock — Step 2 of 3*\n\n"
        f"• *Selected Category:* Day {day_num} Old\n"
        f"• *Current Available Stock:* `{available} unit(s)`\n\n"
        f"Please reply with the *quantity* of units you wish to remove (e.g. `5` or `{available}`):\n\n"
        f"_Send /cancel to abort at any time._"
    )
    if query.message:
        await query.message.edit_text(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminRemoveStockState.waiting_for_quantity, F.text)
async def handle_admin_remove_quantity_input(message: Message, state: FSMContext) -> None:
    """Validate removal quantity and show confirmation preview."""
    user = message.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer("❌ Stock removal cancelled.", reply_markup=get_admin_main_keyboard())
        return

    try:
        qty = int(raw_text)
        if qty <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("⚠️ Please enter a valid positive integer for quantity (e.g. `5`):")
        return

    data = await state.get_data()
    day_num = data.get("day_number", 1)
    avail = data.get("available_qty", 0)

    if qty > avail:
        await message.answer(
            f"⚠️ Requested quantity `{qty}` exceeds available stock `{avail}` in Day {day_num} Old.\n"
            f"Please enter an amount up to `{avail}` units:"
        )
        return

    await state.update_data(quantity=qty)
    await state.set_state(AdminRemoveStockState.waiting_for_confirm)

    preview = (
        "⚠️ *CONFIRM STOCK REMOVAL*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• *Category:* Day {day_num} Old\n"
        f"• *Quantity to Remove:* `{qty} unit(s)`\n"
        f"• *Current Available:* `{avail} unit(s)`\n"
        f"• *Remaining Available:* `{avail - qty} unit(s)`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Records will be marked as REMOVED and excluded from active inventory. "
        "Audit history and change logs will be safely preserved.\n\n"
        "Are you sure you want to proceed?"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑️ Confirm & Remove", callback_data="rem_confirm")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="admin:cancel")]
    ])
    await message.answer(preview, parse_mode="Markdown", reply_markup=kb)


@admin_stock_router.callback_query(AdminRemoveStockState.waiting_for_confirm, F.data == "rem_confirm")
async def handle_admin_confirm_remove_stock(query: CallbackQuery, state: FSMContext) -> None:
    """Execute removal transaction and refresh stock counts immediately."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    data = await state.get_data()
    day_num = data.get("day_number", 1)
    qty = data.get("quantity", 0)

    await state.clear()
    await query.answer("Removing stock...", show_alert=False)

    ok, msg, remaining = StockTxtService.remove_stock_by_day(
        day_number=day_num,
        quantity=qty
    )

    if not ok:
        await query.message.edit_text(f"❌ Removal failed: {msg}", reply_markup=get_admin_main_keyboard())
        return

    success_text = (
        "✅ *STOCK REMOVED SUCCESSFULLY*\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• *Category:* Day {day_num} Old\n"
        f"• *Units Removed:* `{qty}`\n"
        f"• *Remaining in Category:* `{remaining} unit(s)`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Stock counts have been updated immediately. "
        "All removed records are marked as REMOVED in the audit history."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➖ Remove More Stock", callback_data="admin:remove_stock")],
        [InlineKeyboardButton(text="« Back to Admin Panel", callback_data="admin:main")]
    ])

    if query.message:
        await query.message.edit_text(success_text, parse_mode="Markdown", reply_markup=kb)


# ==========================================
# Import History
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:import_history")
async def handle_admin_import_history(query: CallbackQuery) -> None:
    """Display recent stock import audit records."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()

    async with AsyncSessionLocal() as db:
        stmt = select(StockImportHistory).order_by(desc(StockImportHistory.created_at)).limit(10)
        history = list((await db.execute(stmt)).scalars().all())

    lines = [
        "📜 *STOCK IMPORT AUDIT HISTORY*",
        "━━━━━━━━━━━━━━━━━━"
    ]

    if history:
        for r in history:
            date_str = r.created_at.strftime("%Y-%m-%d %H:%M UTC")
            lines.append(
                f"• `{date_str}` | `{r.filename}`\n"
                f"  ✅ Valid: `{r.valid_count}` | 🔄 Duplicates: `{r.duplicate_count}` | ❌ Invalid: `{r.invalid_count}`\n"
                f"  Admin ID: `{r.admin_id}` | Status: `{r.status}`\n"
            )
    else:
        lines.append("No stock import history recorded yet.")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:import_history")],
        [InlineKeyboardButton(text="« Back to Admin Panel", callback_data="admin:main")]
    ])

    if query.message:
        await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)


# ==========================================
# Pending Fulfilment Queue & Admin Actions
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:pending_fulfillment")
async def handle_admin_pending_fulfillment(query: CallbackQuery) -> None:
    """Display orders in PENDING_FULFILLMENT state with resolution actions."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()

    async with AsyncSessionLocal() as db:
        pending_orders = await DeliveryService.get_pending_fulfillments(db, limit=10)

    if not pending_orders:
        text = (
            "✅ *NO PENDING FULFILMENTS*\n\n"
            "All customer purchases are completed and fulfilled!\n"
            "No orders are currently waiting in the queue."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:pending_fulfillment")],
            [InlineKeyboardButton(text="« Back to Admin Panel", callback_data="admin:main")]
        ])
        if query.message:
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    lines = [
        f"⚠️ *PENDING FULFILMENT QUEUE ({len(pending_orders)} order(s))*",
        "━━━━━━━━━━━━━━━━━━",
        "The following orders were paid via customer wallet but require fulfillment resolution:\n"
    ]

    action_buttons = []
    for ord in pending_orders:
        created_str = ord.created_at.strftime("%Y-%m-%d %H:%M")
        uname = f"@{ord.telegram_username}" if ord.telegram_username else f"User {ord.telegram_user_id}"
        lines.append(
            f"• `{ord.order_code}` | {uname}\n"
            f"  💰 `{ord.currency} {ord.total_amount:,.0f}` | 🕒 `{created_str}`\n"
        )
        action_buttons.append([
            InlineKeyboardButton(
                text=f"⚡ Mark Fulfilled: {ord.order_code}",
                callback_data=f"admin:fulfill:{ord.order_code}"
            )
        ])

    action_buttons.append([
        InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:pending_fulfillment"),
        [InlineKeyboardButton(text="« Back to Admin Panel", callback_data="admin:main")][0]
    ])

    if query.message:
        await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=action_buttons))


@admin_stock_router.callback_query(F.data.startswith("admin:fulfill:"))
async def handle_admin_fulfill_order(query: CallbackQuery) -> None:
    """Admin action to mark a pending order as fulfilled."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    order_code = query.data.split(":")[2]

    async with AsyncSessionLocal() as db:
        ok, msg, order = await DeliveryService.fulfill_order(
            db=db,
            order_code=order_code,
            admin_id=user_id,
            fulfillment_notes=f"Resolved manually by Admin {user_id}"
        )

    if ok:
        await query.answer(f"✅ Order {order_code} fulfilled successfully!", show_alert=True)
    else:
        await query.answer(f"❌ {msg}", show_alert=True)

    # Re-render pending list
    await handle_admin_pending_fulfillment(query)


# ==========================================
# Review Fund Requests (Deposits)
# ==========================================
@admin_stock_router.callback_query(F.data.in_(["admin:review_funds", "admin:pending_payments"]))
async def handle_admin_review_funds(query: CallbackQuery) -> None:
    """List pending customer deposit requests awaiting admin verification."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await query.answer()
    async with AsyncSessionLocal() as db:
        pending_deposits = await WalletService.get_pending_deposits(db, limit=10)

    if not pending_deposits:
        text = (
            "💳 *Review Pending Deposits*\n\n"
            "✅ All clear! There are currently no pending deposit requests."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin:pending_payments")],
            [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
        ])
        if query.message:
            await query.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    # Present pending requests with individual action buttons
    lines = [
        f"💳 *Pending Deposit Requests ({len(pending_deposits)})*\n",
        "Verify each transfer against genuine bank/UPI statements before approving:\n"
    ]
    buttons = []
    for dep in pending_deposits:
        created_str = dep.created_at.strftime("%Y-%m-%d %H:%M")
        utr_str = f" | UTR: `{dep.utr_number}`" if dep.utr_number else ""
        ss_str = " | 📸 Has Screenshot" if dep.screenshot_file_id else ""
        lines.append(
            f"• *{dep.reference}* - `{dep.currency} {dep.amount:,.2f}`\n"
            f"  User: `{dep.telegram_user_id}`{utr_str}{ss_str} ({created_str})\n"
        )
        buttons.append([
            InlineKeyboardButton(text=f"✅ Approve {dep.reference}", callback_data=f"dep_app:{dep.reference}"),
            InlineKeyboardButton(text=f"❌ Reject {dep.reference}", callback_data=f"dep_rej:{dep.reference}")
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Refresh List", callback_data="admin:pending_payments"),
        InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")
    ])

    if query.message:
        await query.message.edit_text("\n".join(lines), parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@admin_stock_router.callback_query(F.data.startswith("dep_app:"))
@admin_stock_router.callback_query(F.data.startswith("adm_app_pay:"))
async def handle_admin_approve_deposit(query: CallbackQuery) -> None:
    """Approve customer deposit, credit wallet balance atomically, and record immutable ledger."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    dep_ref = query.data.split(":")[1]

    async with AsyncSessionLocal() as db:
        success, message, deposit = await WalletService.approve_deposit(
            db=db,
            deposit_ref=dep_ref,
            admin_id=user_id,
            admin_notes=f"Approved by Telegram Admin {user_id}"
        )

    if not success:
        await query.answer(f"⚠️ {message}", show_alert=True)
        return

    await query.answer("✅ Deposit approved & credited!", show_alert=False)

    # Update admin message text
    confirm_text = (
        f"✅ *Deposit Approved Successfully!*\n\n"
        f"🔖 Reference: `{deposit.reference}`\n"
        f"👤 Customer ID: `{deposit.telegram_user_id}`\n"
        f"💰 Credited: `{deposit.currency} {deposit.amount:,.2f}`\n"
        f"🕒 Approved At: `{deposit.processed_at.strftime('%Y-%m-%d %H:%M UTC')}`"
    )
    if query.message:
        try:
            await query.message.edit_text(
                confirm_text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Back to Pending List", callback_data="admin:pending_payments")],
                    [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
                ])
            )
        except Exception:
            pass

    # Notify customer proactively with exact Part 12 required format
    if query.bot and deposit:
        customer_alert = (
            "✅ *Payment Approved*\n\n"
            f"Amount added: ₹{deposit.amount:,.2f}\n\n"
            "Your wallet has been updated successfully.\n\n"
            f"Payment Request: {deposit.reference}"
        )
        try:
            await query.bot.send_message(
                chat_id=deposit.telegram_user_id,
                text=customer_alert,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send deposit approval notification to customer {deposit.telegram_user_id}: {e}")


@admin_stock_router.callback_query(F.data.startswith("dep_rej:"))
@admin_stock_router.callback_query(F.data.startswith("adm_rej_pay:"))
async def handle_admin_reject_deposit(query: CallbackQuery) -> None:
    """Reject customer deposit request without modifying wallet balance."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    dep_ref = query.data.split(":")[1]
    reason = "Payment could not be verified."

    async with AsyncSessionLocal() as db:
        success, message, deposit = await WalletService.reject_deposit(
            db=db,
            deposit_ref=dep_ref,
            admin_id=user_id,
            reason=reason
        )

    if not success:
        await query.answer(f"⚠️ {message}", show_alert=True)
        return

    await query.answer("❌ Deposit rejected.", show_alert=True)

    if query.message:
        try:
            await query.message.edit_text(
                f"❌ *Deposit Rejected*\nReference: `{deposit.reference}`\nReason: `{reason}`",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Back to Pending List", callback_data="admin:pending_payments")],
                    [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
                ])
            )
        except Exception:
            pass

    # Notify customer proactively with exact Part 12 required format
    if query.bot and deposit:
        customer_alert = (
            "❌ *Payment Request Rejected*\n\n"
            f"Reason: {reason}\n\n"
            "Your wallet has not been credited. Contact the admin if you believe this is an error."
        )
        try:
            await query.bot.send_message(
                chat_id=deposit.telegram_user_id,
                text=customer_alert,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send deposit rejection notification to customer {deposit.telegram_user_id}: {e}")

    async with AsyncSessionLocal() as db:
        success, message, deposit = await WalletService.reject_deposit(
            db=db,
            deposit_ref=dep_ref,
            admin_id=user_id,
            reason="Deposit could not be verified against official bank/UPI records."
        )

    if not success:
        await query.answer(f"⚠️ {message}", show_alert=True)
        return

    await query.answer("Deposit rejected.", show_alert=False)

    reject_text = (
        f"❌ *Deposit Request Rejected*\n\n"
        f"🔖 Reference: `{deposit.reference}`\n"
        f"👤 Customer ID: `{deposit.telegram_user_id}`\n"
        f"💰 Amount: `{deposit.currency} {deposit.amount:,.0f}`\n"
        f"Status: `REJECTED`"
    )
    if query.message:
        await query.message.edit_text(
            reject_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💳 Back to Pending List", callback_data="admin:review_funds")],
                [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
            ])
        )

    # Notify customer
    if query.bot and deposit:
        customer_alert = (
            "⚠️ *Deposit Request Update*\n\n"
            f"🔖 Reference: `{deposit.reference}`\n"
            f"💰 Amount: `{deposit.currency} {deposit.amount:,.0f}`\n\n"
            "Your deposit request could not be verified and has been rejected.\n"
            "If you believe this is an error, please contact support with genuine bank reference documentation."
        )
        try:
            await query.bot.send_message(
                chat_id=deposit.telegram_user_id,
                text=customer_alert,
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send rejection notification to customer {deposit.telegram_user_id}: {e}")


# ==========================================
# Update Business UPI / QR Settings
# ==========================================
@admin_stock_router.callback_query(F.data == "admin:upi_settings")
async def handle_admin_upi_settings_menu(query: CallbackQuery, state: FSMContext) -> None:
    """Display active UPI settings and provide update buttons."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    await state.clear()
    await query.answer()

    async with AsyncSessionLocal() as db:
        info = await PaymentSettingService.get_settings(db)

    qr_status = "Uploaded (photo configured)" if info.get("qr_image") else "None configured"
    text = (
        "⚙️ *Business Payment Configuration*\n\n"
        f"🆔 *Official UPI ID:* `{info['upi_id']}`\n"
        f"👤 *Payee Name:* `{info['payee_name']}`\n"
        f"🖼️ *QR Code Image:* `{qr_status}`\n"
        f"📋 *Customer Instructions:*\n{info['instructions']}\n\n"
        "Select a field to modify:"
    )
    if query.message:
        await query.message.edit_text(text, parse_mode="Markdown", reply_markup=get_admin_upi_settings_keyboard())


@admin_stock_router.callback_query(F.data == "upi_edit:id")
async def handle_edit_upi_id_click(query: CallbackQuery, state: FSMContext) -> None:
    user = query.from_user
    if not check_admin(user.id if user else 0):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    await query.answer()
    await state.set_state(AdminUpiSettingsState.waiting_for_upi_id)
    prompt = "🆔 *Update UPI ID*\n\nPlease reply with the new official business UPI ID (e.g. `business@okhdfcbank`):"
    if query.message:
        await query.message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminUpiSettingsState.waiting_for_upi_id)
async def process_update_upi_id(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    new_id = (message.text or "").strip()
    if not new_id or "@" not in new_id:
        await message.answer("⚠️ Please provide a valid UPI ID containing `@` (e.g. `store@upi`):")
        return

    await state.clear()
    async with AsyncSessionLocal() as db:
        await PaymentSettingService.update_setting(
            db=db,
            key=PaymentSettingService.SETTING_UPI_ID,
            value=new_id,
            admin_id=user_id,
            description="Business UPI ID"
        )

    await message.answer(
        f"✅ Official UPI ID updated to: `{new_id}`",
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


@admin_stock_router.callback_query(F.data == "upi_edit:name")
async def handle_edit_payee_name_click(query: CallbackQuery, state: FSMContext) -> None:
    user = query.from_user
    if not check_admin(user.id if user else 0):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    await query.answer()
    await state.set_state(AdminUpiSettingsState.waiting_for_payee_name)
    prompt = "👤 *Update Payee Name*\n\nPlease reply with the new business payee name:"
    if query.message:
        await query.message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminUpiSettingsState.waiting_for_payee_name)
async def process_update_payee_name(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer("⚠️ Payee name cannot be empty. Please enter a valid name:")
        return

    await state.clear()
    async with AsyncSessionLocal() as db:
        await PaymentSettingService.update_setting(
            db=db,
            key=PaymentSettingService.SETTING_PAYEE_NAME,
            value=new_name,
            admin_id=user_id,
            description="Business Payee Name"
        )

    await message.answer(
        f"✅ Business Payee Name updated to: `{new_name}`",
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


@admin_stock_router.callback_query(F.data == "upi_edit:inst")
async def handle_edit_instructions_click(query: CallbackQuery, state: FSMContext) -> None:
    user = query.from_user
    if not check_admin(user.id if user else 0):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    await query.answer()
    await state.set_state(AdminUpiSettingsState.waiting_for_instructions)
    prompt = "📝 *Update Customer Instructions*\n\nPlease reply with the new customer payment instructions:"
    if query.message:
        await query.message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminUpiSettingsState.waiting_for_instructions)
async def process_update_instructions(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    new_inst = (message.text or "").strip()
    if not new_inst:
        await message.answer("⚠️ Instructions cannot be empty. Please enter instructions:")
        return

    await state.clear()
    async with AsyncSessionLocal() as db:
        await PaymentSettingService.update_setting(
            db=db,
            key=PaymentSettingService.SETTING_INSTRUCTIONS,
            value=new_inst,
            admin_id=user_id,
            description="Customer Payment Instructions"
        )

    await message.answer(
        "✅ Payment instructions updated successfully.",
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


@admin_stock_router.callback_query(F.data == "upi_edit:qr")
async def handle_edit_qr_click(query: CallbackQuery, state: FSMContext) -> None:
    user = query.from_user
    if not check_admin(user.id if user else 0):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return
    await query.answer()
    await state.set_state(AdminUpiSettingsState.waiting_for_qr)
    prompt = (
        "🖼️ *Update QR Code Image*\n\n"
        "Please send a new photo of your official UPI QR code, or reply `/clear` to remove the current image:"
    )
    if query.message:
        await query.message.answer(prompt, parse_mode="Markdown")


@admin_stock_router.message(AdminUpiSettingsState.waiting_for_qr, F.photo)
async def process_update_qr_photo(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    photo_id = message.photo[-1].file_id
    await state.clear()

    async with AsyncSessionLocal() as db:
        await PaymentSettingService.update_setting(
            db=db,
            key=PaymentSettingService.SETTING_QR_IMAGE,
            value=photo_id,
            admin_id=user_id,
            description="Telegram File ID for UPI QR Code"
        )

    await message.answer(
        "✅ UPI QR Code photo updated successfully.",
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


@admin_stock_router.message(AdminUpiSettingsState.waiting_for_qr, F.text.lower() == "/clear")
async def process_clear_qr(message: Message, state: FSMContext) -> None:
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    await state.clear()
    async with AsyncSessionLocal() as db:
        await PaymentSettingService.update_setting(
            db=db,
            key=PaymentSettingService.SETTING_QR_IMAGE,
            value="",
            admin_id=user_id,
            description="Cleared QR code"
        )

    await message.answer(
        "✅ UPI QR Code image cleared.",
        parse_mode="Markdown",
        reply_markup=get_admin_main_keyboard()
    )


# ==========================================
# TXT Stock Import System Handlers
# ==========================================
def format_preview_text(preview: ImportPreview) -> str:
    """Format safe HTML message for stock import preview."""
    import html
    lines = [
        "📋 <b>Stock Import Preview</b>",
        f"📁 <b>Source:</b> <code>{html.escape(preview.filename)}</code>",
        "",
        "📊 <b>Summary Statistics:</b>",
        f"• Total Lines Detected: <code>{preview.total_lines_read}</code>",
        f"• ✅ Valid New Records: <code>{preview.total_valid}</code>",
        f"• 🔄 Duplicate Records Skipped: <code>{preview.total_duplicates}</code>",
        f"• ❌ Invalid Lines: <code>{preview.total_invalid}</code>",
        f"• ⚠️ Records Needing Review: <code>{preview.total_review}</code>",
        "",
    ]

    if preview.category_counts:
        lines.append("📅 <b>Category & Date Grouping (Valid Records):</b>")
        for cat, data in preview.category_counts.items():
            dates_str = ", ".join(data["dates"]) if data["dates"] else "N/A"
            lines.append(f"• <b>{html.escape(cat)}:</b> <code>{data['count']} unit(s)</code> (Dates: {html.escape(dates_str)})")
        lines.append("")

    if preview.valid_records:
        lines.append("🔎 <b>Sample Items Detected:</b>")
        for r in preview.valid_records[:3]:
            time_str = r.created_at.strftime('%Y-%m-%d %H:%M UTC')
            lines.append(f"• <code>{html.escape(r.item_id)}</code> → <b>{html.escape(r.category_label)}</b> ({time_str})")
        if len(preview.valid_records) > 3:
            lines.append(f"<i>...and {len(preview.valid_records) - 3} more items.</i>")
        lines.append("")

    if preview.total_valid > 0:
        lines.append(f"✨ Ready to import <b>{preview.total_valid} valid units</b>. Tap <b>Confirm Import</b> below to commit to database.")
    else:
        lines.append("⚠️ <b>No valid records found.</b> Please inspect issues or upload a corrected file/paste.")

    return chr(10).join(lines)
def format_errors_text(preview: ImportPreview) -> str:
    """Format detailed error, duplicate, and review-required report with safe HTML."""
    import html
    lines = [
        "⚠️ <b>Import Issues & Exclusions Breakdown</b>",
        f"📁 <b>Filename:</b> <code>{html.escape(preview.filename)}</code>",
        "",
    ]

    if preview.invalid_records:
        lines.append(f"❌ <b>Invalid Lines ({len(preview.invalid_records)}):</b>")
        for r in preview.invalid_records[:15]:
            lines.append(f"• Line {r.line_number}: <code>{html.escape(r.item_id)}</code> — {html.escape(str(r.error_reason))}")
        if len(preview.invalid_records) > 15:
            lines.append(f"<i>...and {len(preview.invalid_records) - 15} more invalid lines.</i>")
        lines.append("")

    if preview.duplicates:
        lines.append(f"🔄 <b>Duplicate Records Skipped ({len(preview.duplicates)}):</b>")
        for r in preview.duplicates[:15]:
            lines.append(f"• Line {r.line_number}: <code>{html.escape(r.item_id)}</code> — {html.escape(str(r.error_reason))}")
        if len(preview.duplicates) > 15:
            lines.append(f"<i>...and {len(preview.duplicates) - 15} more duplicates.</i>")
        lines.append("")

    if preview.review_records:
        lines.append(f"⚠️ <b>Records Needing Review ({len(preview.review_records)}):</b>")
        for r in preview.review_records[:15]:
            lines.append(f"• Line {r.line_number}: <code>{html.escape(r.item_id)}</code> — {html.escape(str(r.error_reason))}")
        if len(preview.review_records) > 15:
            lines.append(f"<i>...and {len(preview.review_records) - 15} more review records.</i>")
        lines.append("")

    if not preview.invalid_records and not preview.duplicates and not preview.review_records:
        lines.append("✅ No issues found! All lines in this file are completely valid.")

    return chr(10).join(lines)
@admin_stock_router.callback_query(F.data == "admin:import_txt")
@admin_stock_router.message(Command("import_stock"))
async def handle_import_txt_menu(event: Message | CallbackQuery, state: FSMContext) -> None:
    """Initiate TXT file stock import flow."""
    user = event.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        else:
            await event.answer("⛔ Unauthorized: Administrator access required.")
        return

    await state.clear()
    await state.set_state(AdminImportTxtState.waiting_for_file)

    instructions = (
        "📁 *Import Stock via TXT File*\n\n"
        "Upload a `.txt` file or paste inventory lines directly into this chat.\n\n"
        "📋 *Supported Formats:*\n"
        "• *Format 1 (Account Format):*\n"
        "  `mail:pass:username:date:time`\n"
        "  _Example:_ `( neha6501@metamail.store:654654@@:neha404714:11 Oct 2026, 01:14:43 IST )`\n\n"
        "• *Format 2 (Pipe Full):*\n"
        "  `ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS`\n"
        "  _Example:_ `DEMO-0001|DAY_1_OLD|2026-10-18T12:00:00Z|AVAILABLE`\n\n"
        "• *Format 3 (Pipe Compact — Auto-Aged):*\n"
        "  `ITEM_ID|CREATED_AT_UTC`\n"
        "  _Example:_ `DEMO-0002|2026-10-17T15:30:00Z`\n\n"
        "⚙️ *Import Rules:*\n"
        "• UTF-8 and UTF-8 BOM files supported\n"
        "• Blank lines and comments (`#`) are ignored\n"
        "• Exact age is calculated using complete 24-hour periods\n"
        "• Automatically groups into Day 1 Old through Day 20 Old\n"
        "• Out-of-range (>20 days or future) flagged for review\n"
        "• Duplicate item IDs in file or DB are safely skipped\n"
        "• Max file size: 5 MB | Max lines: 10,000\n\n"
        "📎 *Please attach and send your `.txt` file or paste lines now:*"
    )

    cancel_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Cancel Import", callback_data="admin:cancel")]
    ])

    if isinstance(event, CallbackQuery):
        await event.answer()
        if event.message:
            await event.message.edit_text(instructions, parse_mode="Markdown", reply_markup=cancel_kb)
    else:
        await event.answer(instructions, parse_mode="Markdown", reply_markup=cancel_kb)


@admin_stock_router.message(AdminImportTxtState.waiting_for_file, F.document)
@admin_stock_router.message(F.document)
async def handle_import_txt_file(message: Message, state: FSMContext) -> None:
    """Process uploaded TXT document, validate lines, check duplicates, and display preview."""
    user = message.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        current_state = await state.get_state()
        if current_state == AdminImportTxtState.waiting_for_file.state:
            await message.answer("⛔ Unauthorized: Administrator access required.")
        return

    doc = message.document
    if not doc:
        return

    filename = doc.file_name or "stock_upload.txt"
    if not filename.lower().endswith(".txt"):
        current_state = await state.get_state()
        if current_state == AdminImportTxtState.waiting_for_file.state:
            await message.answer(
                "⚠️ Invalid file type. Please upload a plain text file ending in <code>.txt</code>.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Cancel", callback_data="admin:cancel", style="danger")]
                ])
            )
        return

    if doc.file_size and doc.file_size > MAX_IMPORT_FILE_SIZE_BYTES:
        await message.answer(
            f"⚠️ File exceeds the maximum size limit of 5 MB ({doc.file_size / (1024*1024):.1f} MB).",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="« Cancel", callback_data="admin:cancel", style="danger")]
            ])
        )
        return

    status_msg = await message.answer("⏳ Downloading and parsing inventory file...")

    try:
        file_io = await message.bot.download(doc)
        if hasattr(file_io, "getvalue"):
            content_bytes = file_io.getvalue()
        elif hasattr(file_io, "read"):
            file_io.seek(0)
            content_bytes = file_io.read()
        elif isinstance(file_io, bytes):
            content_bytes = file_io
        else:
            content_bytes = b""
    except Exception as e:
        logger.error(f"Failed to download document: {e}")
        await status_msg.edit_text(f"❌ Failed to download file from Telegram: {e}")
        return

    async with AsyncSessionLocal() as db:
        preview = await StockImportService.parse_txt_file(
            content_bytes=content_bytes,
            filename=filename,
            db=db
        )

    await state.clear()
    preview_text = format_preview_text(preview)
    has_issues = (preview.total_duplicates > 0 or preview.total_invalid > 0 or preview.total_review > 0)
    keyboard = get_import_preview_keyboard(preview.session_id, has_issues=has_issues)

    try:
        await status_msg.edit_text(preview_text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Failed to edit status_msg with preview: {e}")
        await message.answer(preview_text, parse_mode="HTML", reply_markup=keyboard)
@admin_stock_router.message(AdminImportTxtState.waiting_for_file)
async def handle_import_txt_non_document(message: Message, state: FSMContext) -> None:
    """Handle direct text lines or prompt admin to send a document if photo or invalid input is sent."""
    user = message.from_user
    user_id = user.id if user else 0
    if not check_admin(user_id):
        return

    raw_text = (message.text or "").strip()
    if raw_text.lower() in ("/cancel", "cancel"):
        await state.clear()
        await message.answer("❌ Stock import cancelled.", reply_markup=get_admin_main_keyboard())
        return

    # If the user pasted lines directly into chat instead of attaching a file
    if ":" in raw_text or "|" in raw_text:
        status_msg = await message.answer("⏳ Parsing inventory lines...")
        async with AsyncSessionLocal() as db:
            preview = await StockImportService.parse_txt_file(
                content_bytes=raw_text.encode("utf-8"),
                filename="direct_text_entry.txt",
                db=db
            )

        await state.clear()
        preview_text = format_preview_text(preview)
        has_issues = (preview.total_duplicates > 0 or preview.total_invalid > 0 or preview.total_review > 0)
        keyboard = get_import_preview_keyboard(preview.session_id, has_issues=has_issues)
        try:
            await status_msg.edit_text(preview_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await message.answer(preview_text, parse_mode="HTML", reply_markup=keyboard)
        return

    await message.answer(
        "📎 Please send your inventory file as a **Document attachment** (`.txt` file) or paste lines in `mail:pass:username:date:time` format.\n"
        "Send /cancel to return to main menu.",
        parse_mode="Markdown"
    )


@admin_stock_router.callback_query(F.data.startswith("imp_confirm:"))
async def handle_import_confirm(query: CallbackQuery) -> None:
    """Commit valid records from preview session into database."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized: Administrator access required.", show_alert=True)
        return

    session_id = query.data.split(":")[1]
    preview = StockImportService.get_session(session_id)
    if not preview:
        await query.answer("⚠️ Session expired. Please re-upload your file.", show_alert=True)
        return

    await query.answer("Saving inventory records...", show_alert=False)

    async with AsyncSessionLocal() as db:
        success, msg, count = await StockImportService.commit_import(
            session_id=session_id,
            admin_id=user_id,
            db=db
        )

    if not success:
        if query.message:
            await query.message.edit_text(
                f"❌ <b>Import Failed</b>\n\n{html.escape(msg)}\n\nAll changes were safely rolled back.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main", style="danger")]
                ])
            )
        return

    success_text = (
        "🎉 <b>Stock Import Confirmed & Saved!</b>\n\n"
        f"📁 <b>Filename:</b> <code>{html.escape(preview.filename)}</code>\n"
        f"✅ <b>Records Added:</b> <code>{count} units</code>\n"
        f"🔄 <b>Duplicates Skipped:</b> <code>{preview.total_duplicates}</code>\n"
        f"🕒 <b>Committed At:</b> <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</code>\n\n"
        "All valid records are now live and immediately purchasable in <b>🛒 Buy Indo</b> and visible in <b>📦 Total Stock</b>."
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 Import Another TXT", callback_data="admin:import_txt", style="primary")],
        [InlineKeyboardButton(text="📦 View Live Stock", callback_data="admin:view_stock", style="primary")],
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main", style="danger")]
    ])

    if query.message:
        await query.message.edit_text(success_text, parse_mode="HTML", reply_markup=kb)


@admin_stock_router.callback_query(F.data.startswith("imp_cancel:"))
async def handle_import_cancel(query: CallbackQuery) -> None:
    """Cancel stock import session without saving any records."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    session_id = query.data.split(":")[1]
    StockImportService.cancel_import(session_id, admin_id=user_id)

    await query.answer("Import cancelled.")
    text = (
        "❌ <b>Stock Import Cancelled</b>\n\n"
        "The import session has been discarded. No database records were modified."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main", style="danger")]
    ])
    if query.message:
        try:
            await query.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


@admin_stock_router.callback_query(F.data.startswith("imp_errors:"))
async def handle_import_errors(query: CallbackQuery) -> None:
    """Display breakdown of invalid lines, duplicates, and review records."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    session_id = query.data.split(":")[1]
    preview = StockImportService.get_session(session_id)
    if not preview:
        await query.answer("⚠️ Session expired. Please re-upload your file.", show_alert=True)
        return

    await query.answer()
    errors_text = format_errors_text(preview)
    kb = get_import_errors_keyboard(session_id)

    if query.message:
        try:
            await query.message.edit_text(errors_text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass


@admin_stock_router.callback_query(F.data.startswith("imp_back:"))
async def handle_import_back_to_preview(query: CallbackQuery) -> None:
    """Return from errors breakdown to import preview screen."""
    user = query.from_user
    user_id = user.id if user else 0

    if not check_admin(user_id):
        await query.answer("⛔ Unauthorized", show_alert=True)
        return

    session_id = query.data.split(":")[1]
    preview = StockImportService.get_session(session_id)
    if not preview:
        await query.answer("⚠️ Session expired. Please re-upload your file.", show_alert=True)
        return

    await query.answer()
    preview_text = format_preview_text(preview)
    has_issues = (preview.total_duplicates > 0 or preview.total_invalid > 0 or preview.total_review > 0)
    kb = get_import_preview_keyboard(session_id, has_issues=has_issues)

    if query.message:
        try:
            await query.message.edit_text(preview_text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

