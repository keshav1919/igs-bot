"""
Catalog browsing, categories, day-based grouping, and item listing handlers.
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from sqlalchemy import select
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.bot.keyboards import (
    get_categories_keyboard,
    get_day_groups_keyboard,
    get_items_keyboard,
    get_item_detail_keyboard
)

catalog_router = Router()

@catalog_router.message(F.text == "🛍️ Browse Catalog")
@catalog_router.message(Command("catalog"))
async def handle_browse_catalog(message: Message) -> None:
    """Display active categories to customer."""
    async with AsyncSessionLocal() as db:
        stmt = select(Category).where(Category.is_active == True).order_by(Category.sort_order, Category.name)
        categories = list((await db.execute(stmt)).scalars().all())

    if not categories:
        await message.answer("ℹ️ No active categories found in inventory right now. Please check back shortly!")
        return

    text = "📁 *SELECT INVENTORY CATEGORY:*\nChoose a category below to explore available listings:"
    await message.answer(text, parse_mode="Markdown", reply_markup=get_categories_keyboard(categories))

@catalog_router.callback_query(F.data == "back_to_cats")
async def handle_back_to_cats(callback: CallbackQuery) -> None:
    """Return to category selection."""
    async with AsyncSessionLocal() as db:
        stmt = select(Category).where(Category.is_active == True).order_by(Category.sort_order, Category.name)
        categories = list((await db.execute(stmt)).scalars().all())

    await callback.message.edit_text(
        "📁 *SELECT INVENTORY CATEGORY:*\nChoose a category below to explore available listings:",
        parse_mode="Markdown",
        reply_markup=get_categories_keyboard(categories)
    )
    await callback.answer()

@catalog_router.callback_query(F.data.startswith("cat:"))
async def handle_category_selected(callback: CallbackQuery) -> None:
    """Category selected: display available day-based groups (e.g. Day 1 - Day 10)."""
    cat_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as db:
        cat = await db.get(Category, cat_id)
        if not cat:
            await callback.answer("Category not found.", show_alert=True)
            return

        # Query distinct day groups available in this category
        stmt = (
            select(InventoryItem.day_group)
            .where(
                InventoryItem.category_id == cat_id,
                InventoryItem.status == "active"
            )
            .distinct()
            .order_by(InventoryItem.day_group)
        )
        found_groups = list((await db.execute(stmt)).scalars().all())

    # Fallback to standard Day 1 - Day 10 if no specific items yet
    day_groups = found_groups if found_groups else [f"Day {i}" for i in range(1, 11)]

    text = (
        f"🏷️ *Category:* {cat.icon} *{cat.name}*\n"
        f"_{cat.description or 'Authentic digital inventory'}_\n\n"
        f"📅 *Select Day / Aging Group:*\n"
        f"Filter listings by legitimate batch maturation (Day 1 through Day 10) or view all:"
    )

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_day_groups_keyboard(cat_id, day_groups)
    )
    await callback.answer()

@catalog_router.callback_query(F.data.startswith("filter_dg:"))
async def handle_day_group_filter(callback: CallbackQuery) -> None:
    """Filter items by category and day group."""
    parts = callback.data.split(":")
    cat_id = int(parts[1])
    day_group = parts[2]

    async with AsyncSessionLocal() as db:
        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.category_id == cat_id,
                InventoryItem.status == "active"
            )
        )
        if day_group != "all":
            stmt = stmt.where(InventoryItem.day_group == day_group)

        stmt = stmt.order_by(InventoryItem.price)
        items = list((await db.execute(stmt)).scalars().all())

    dg_label = "All Batches" if day_group == "all" else day_group
    if not items:
        text = f"ℹ️ *No active items found* under *{dg_label}* in this category.\nPlease try another day group."
        await callback.message.edit_text(
            text,
            parse_mode="Markdown",
            reply_markup=get_day_groups_keyboard(cat_id, [f"Day {i}" for i in range(1, 11)])
        )
        await callback.answer()
        return

    text = (
        f"📋 *Available Listings* | {dg_label}\n"
        f"Select an item to view price, stock, and ordering details:"
    )
    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_items_keyboard(items, cat_id, day_group)
    )
    await callback.answer()

@catalog_router.callback_query(F.data.startswith("item:"))
async def handle_item_detail(callback: CallbackQuery) -> None:
    """Display comprehensive details for an individual inventory listing."""
    item_id = int(callback.data.split(":")[1])

    async with AsyncSessionLocal() as db:
        item = await InventoryService.get_by_id(db, item_id)

    if not item:
        await callback.answer("Item listing not found.", show_alert=True)
        return

    stock_text = f"✅ `{item.quantity} units available`" if item.quantity > 0 else "❌ `Out of Stock`"
    alert_tag = " ⚠️ *[Low Stock Alert]*" if item.is_low_stock else ""

    text = (
        f"📦 *{item.title}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *SKU:* `{item.sku}`\n"
        f"🔹 *Category:* {item.category.name if item.category else 'General'}\n"
        f"🔹 *Day Group:* 📅 `{item.day_group}`\n"
        f"🔹 *Availability:* {stock_text}{alert_tag}\n"
        f"🔹 *Price:* `{item.price:,.0f} {item.currency}`\n\n"
        f"📝 *Description:*\n{item.description or 'Authentic verified inventory record.'}\n\n"
        f"🔒 *Fulfillment Guarantee:*\n"
        f"Goods are released instantly upon authorized payment verification."
    )

    await callback.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_item_detail_keyboard(item)
    )
    await callback.answer()
