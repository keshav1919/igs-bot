"""
Inline and Reply Keyboards for aiogram 3 Telegram Bot.
Includes customer 4-button persistent reply keyboard, 20 age-category inline buttons,
deposit actions, and administrator management keyboards.
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem

def get_main_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Bottom persistent navigation bar.
    Contains customer buttons, plus 1 administrator-only button if caller is admin.
    1. 🛒 Buy Indo
    2. 📦 Total Stock
    3. 💰 Add Fund
    4. 👛 My Wallet
    5. 🛡️ Admin Panel (only if is_admin is True)
    """
    keyboard = [
        [KeyboardButton(text="🛒 Buy Indo"), KeyboardButton(text="📦 Total Stock")],
        [KeyboardButton(text="💰 Add Fund"), KeyboardButton(text="👛 My Wallet")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="🛡️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=False)

def get_available_buy_categories_keyboard(available_categories: list[dict]) -> InlineKeyboardMarkup:
    """
    Display ONLY categories with genuine available stock.
    Hides empty categories (Day 1 Old through Day 20 Old).
    Shows only the day-old label without stock count on the button.
    """
    rows = []
    current_row = []
    for cat in available_categories:
        day = cat["day"]
        current_row.append(
            InlineKeyboardButton(
                text=f"📅 Day {day} Old",
                callback_data=f"buy_day:{day}",
                style="primary"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([
        InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_buy_quantity_keyboard(day_number: int) -> InlineKeyboardMarkup:
    """
    Quantity selection for buying accounts in a chosen day category:
    1 igs, 5 igs, 10 igs, 20 igs, custom amount, and back.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 igs", callback_data=f"buy_qty:{day_number}:1", style="primary"),
            InlineKeyboardButton(text="5 igs", callback_data=f"buy_qty:{day_number}:5", style="primary"),
        ],
        [
            InlineKeyboardButton(text="10 igs", callback_data=f"buy_qty:{day_number}:10", style="primary"),
            InlineKeyboardButton(text="20 igs", callback_data=f"buy_qty:{day_number}:20", style="primary"),
        ],
        [
            InlineKeyboardButton(text="✏️ Custom Amount", callback_data=f"buy_custom:{day_number}", style="primary"),
        ],
        [
            InlineKeyboardButton(text="« Back to Categories", callback_data="back_to_buy_indo", style="danger"),
        ],
    ])

def get_buy_indo_keyboard(max_days: int = 20) -> InlineKeyboardMarkup:
    """
    Compact 20 age-category buttons (Day 1 through Day 20) plus back button.
    Used for administrative inspection or fallback.
    """
    rows = []
    current_row = []
    for day in range(1, max_days + 1):
        label = f"{day} Day{'s' if day > 1 else ''} Old"
        current_row.append(
            InlineKeyboardButton(text=f"📅 {label}", callback_data=f"buy_day:{day}")
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([
        InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_age_category_detail_keyboard(
    item_id: int | None,
    day_number: int,
    has_stock: bool,
    can_afford: bool,
    price: float = 0.0,
    currency: str = "IDR"
) -> InlineKeyboardMarkup:
    """
    Action buttons for a selected age category.
    Shows Buy button only when stock is available and balance is sufficient.
    """
    rows = []
    if has_stock and item_id is not None:
        if can_afford:
            rows.append([
                InlineKeyboardButton(
                    text=f"⚡ Buy 1 Unit ({price:,.0f} {currency})",
                    callback_data=f"confirm_buy:{item_id}:{day_number}",
                    style="success"
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="💰 Add Fund (Insufficient Balance)",
                    callback_data="goto_add_fund",
                    style="primary"
                )
            ])
    else:
        rows.append([
            InlineKeyboardButton(text="❌ Out of Stock", callback_data="noop", style="danger")
        ])

    rows.append([
        InlineKeyboardButton(text="« Back to Age Categories", callback_data="back_to_buy_indo", style="danger")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_add_fund_keyboard() -> InlineKeyboardMarkup:
    """Customer actions on the Add Fund screen."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕  Add Fund", callback_data="fund_enter_amount", style="primary")],
        [InlineKeyboardButton(text="History", callback_data="my_transactions", style="primary")],
        [InlineKeyboardButton(text="« Back", callback_data="back_to_main", style="danger")]
    ])

def get_my_wallet_keyboard() -> InlineKeyboardMarkup:
    """Customer actions on the My Wallet screen."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="💰 Add Fund", callback_data="goto_add_fund", style="success"),
            InlineKeyboardButton(text="📜 My Orders", callback_data="orders_list", style="primary")
        ],
        [
            InlineKeyboardButton(text="🔄 Refresh", callback_data="refresh_my_wallet", style="primary"),
            InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")
        ]
    ])

def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    """Main administrative dashboard keyboard strictly matching Part 6 requirements."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Import TXT Stock", callback_data="admin:import_txt")],
        [InlineKeyboardButton(text="📊 View Stock Summary", callback_data="admin:view_stock")],
        [InlineKeyboardButton(text="💵 Price Management", callback_data="admin:manage_prices")],
        [InlineKeyboardButton(text="➖ Remove Stock", callback_data="admin:remove_stock")],
        [InlineKeyboardButton(text="💳 Pending Payments", callback_data="admin:pending_payments")],
        [InlineKeyboardButton(text="⚠️ Pending Fulfilment", callback_data="admin:pending_fulfillment")],
        [InlineKeyboardButton(text="📜 Import & Transaction History", callback_data="admin:import_history")],
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="admin:back_main")]
    ])

def get_deposit_request_keyboard(deposit_ref: str | None = None) -> InlineKeyboardMarkup:
    """
    Customer actions when viewing active payment request with QR code:
    Shows ONLY the Back button. Customer sends screenshot directly in chat.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="cancel_payment", style="danger")]
    ])

def get_admin_deposit_review_keyboard(deposit_ref: str) -> InlineKeyboardMarkup:
    """Admin quick actions for verified approval or rejection of payment."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve Payment", callback_data=f"adm_app_pay:{deposit_ref}", style="success"),
            InlineKeyboardButton(text="❌ Reject Payment", callback_data=f"adm_rej_pay:{deposit_ref}", style="danger")
        ]
    ])

def get_admin_remove_day_keyboard(categories: list[dict]) -> InlineKeyboardMarkup:
    """Admin stock removal: select target day category showing live stock counts."""
    rows = []
    current_row = []
    for cat in categories:
        day = cat["day"]
        qty = cat["quantity"]
        current_row.append(
            InlineKeyboardButton(
                text=f"Day {day} ({qty} pcs)",
                callback_data=f"rem_day:{day}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([
        InlineKeyboardButton(text="« Cancel", callback_data="admin:cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_import_preview_keyboard(session_id: str, has_issues: bool = False) -> InlineKeyboardMarkup:
    """Action buttons for stock import preview."""
    rows = [
        [
            InlineKeyboardButton(text="✅ Confirm Import", callback_data=f"imp_confirm:{session_id}", style="success"),
            InlineKeyboardButton(text="❌ Cancel Import", callback_data=f"imp_cancel:{session_id}", style="danger")
        ]
    ]
    if has_issues:
        rows.append([
            InlineKeyboardButton(text="⚠️ View Errors & Warnings", callback_data=f"imp_errors:{session_id}", style="primary")
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_import_errors_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Navigation buttons from errors view back to import preview."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="« Back to Preview", callback_data=f"imp_back:{session_id}", style="primary"),
            InlineKeyboardButton(text="❌ Cancel Import", callback_data=f"imp_cancel:{session_id}", style="danger")
        ]
    ])

def get_admin_day_selection_keyboard(max_days: int = 20) -> InlineKeyboardMarkup:
    """Admin guided stock addition: select target day category."""
    rows = []
    current_row = []
    for day in range(1, max_days + 1):
        label = f"{day} Day{'s' if day > 1 else ''} Old"
        current_row.append(
            InlineKeyboardButton(text=f"{label}", callback_data=f"adm_day:{day}")
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([
        InlineKeyboardButton(text="« Cancel", callback_data="admin:cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_deposit_action_keyboard(deposit_ref: str) -> InlineKeyboardMarkup:
    """Approve/Reject buttons for a pending deposit."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_app:{deposit_ref}"),
            InlineKeyboardButton(text="❌ Reject", callback_data=f"dep_rej:{deposit_ref}")
        ],
        [
            InlineKeyboardButton(text="« Back to Pending List", callback_data="admin:review_funds")
        ]
    ])

def get_admin_upi_settings_keyboard() -> InlineKeyboardMarkup:
    """Field selection for updating business payment configuration."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆔 Update UPI ID", callback_data="upi_edit:id")],
        [InlineKeyboardButton(text="👤 Update Payee Name", callback_data="upi_edit:name")],
        [InlineKeyboardButton(text="📝 Update Instructions", callback_data="upi_edit:inst")],
        [InlineKeyboardButton(text="🖼️ Update QR Code (Photo)", callback_data="upi_edit:qr")],
        [InlineKeyboardButton(text="« Back to Admin Menu", callback_data="admin:main")]
    ])

# Backward compatibility functions for existing tests
def get_categories_keyboard(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons = []
    for cat in categories:
        buttons.append([
            InlineKeyboardButton(
                text=f"{cat.icon} {cat.name}",
                callback_data=f"cat:{cat.id}"
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_day_groups_keyboard(category_id: int, day_groups: list[str]) -> InlineKeyboardMarkup:
    rows = []
    current_row = []
    for dg in day_groups:
        current_row.append(
            InlineKeyboardButton(
                text=f"📅 {dg}",
                callback_data=f"filter_dg:{category_id}:{dg}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([
        InlineKeyboardButton(text="🌐 Show All Batches / Days", callback_data=f"filter_dg:{category_id}:all")
    ])
    rows.append([
        InlineKeyboardButton(text="⬅️ Back to Categories", callback_data="back_to_cats")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_items_keyboard(items: list[InventoryItem], category_id: int, day_group: str) -> InlineKeyboardMarkup:
    rows = []
    for it in items:
        stock_badge = f"({it.quantity} in stock)" if it.quantity > 0 else "(Out of stock)"
        rows.append([
            InlineKeyboardButton(
                text=f"📦 {it.title} | {it.price:,.0f} {it.currency} {stock_badge}",
                callback_data=f"item:{it.id}"
            )
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ Back to Day Groups", callback_data=f"cat:{category_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_item_detail_keyboard(item: InventoryItem) -> InlineKeyboardMarkup:
    rows = []
    if item.is_available:
        rows.append([
            InlineKeyboardButton(text=f"⚡ Order Now ({item.price:,.0f} {item.currency})", callback_data=f"buy:{item.id}")
        ])
    else:
        rows.append([
            InlineKeyboardButton(text="❌ Out of Stock", callback_data="noop")
        ])
    rows.append([
        InlineKeyboardButton(text="⬅️ Back to Category", callback_data=f"cat:{item.category_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_order_actions_keyboard(order_code: str, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "PENDING_PAYMENT":
        rows.append([
            InlineKeyboardButton(text="💳 Submit Payment Reference", callback_data=f"pay_ref:{order_code}")
        ])
    rows.append([
        InlineKeyboardButton(text="🔄 Refresh Status", callback_data=f"chk_ord:{order_code}"),
        InlineKeyboardButton(text="💬 Order Support", callback_data=f"ord_sup:{order_code}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ==========================================
# Price Management Keyboards
# ==========================================
def get_admin_pricing_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for Price Management submenu."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💵 Set Category Price", callback_data="admin:price:set")],
        [InlineKeyboardButton(text="✏️ Change Price", callback_data="admin:price:change")],
        [InlineKeyboardButton(text="📋 View All Prices", callback_data="admin:price:view_all")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin:back_main")]
    ])


def get_admin_pricing_reply_keyboard() -> ReplyKeyboardMarkup:
    """Persistent reply keyboard with exact requested admin pricing buttons."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💵 Set Category Price"), KeyboardButton(text="✏️ Change Price")],
            [KeyboardButton(text="📋 View All Prices"), KeyboardButton(text="🔙 Back")]
        ],
        resize_keyboard=True
    )


def get_admin_price_category_selector_keyboard(categories_pricing: list[dict], action_prefix: str = "price_sel") -> InlineKeyboardMarkup:
    """Selector keyboard showing 20 day categories with current prices."""
    rows = []
    current_row = []
    for item in categories_pricing:
        day = item["day"]
        price = item["price"]
        # Format compact: e.g. Day 1 (25k)
        if price >= 1000:
            compact_price = f"{int(price/1000)}k"
        else:
            compact_price = f"{int(price)}"
        current_row.append(
            InlineKeyboardButton(
                text=f"Day {day} ({compact_price})",
                callback_data=f"{action_prefix}:{day}"
            )
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    rows.append([
        InlineKeyboardButton(text="« Back to Price Management", callback_data="admin:manage_prices")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_price_confirmation_keyboard(day_number: int, new_price: float, allow_free: bool = False) -> InlineKeyboardMarkup:
    """Review and confirm keyboard before updating category price."""
    free_flag = 1 if allow_free else 0
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Confirm Price",
                callback_data=f"price_confirm:{day_number}:{int(new_price)}:{free_flag}"
            )
        ],
        [
            InlineKeyboardButton(text="❌ Cancel", callback_data="admin:manage_prices")
        ]
    ])
