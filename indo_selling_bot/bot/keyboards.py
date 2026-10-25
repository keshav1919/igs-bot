"""
Inline and Reply Keyboards for aiogram 3 Telegram Bot.
Includes customer 4-button persistent reply keyboard, 20 age-category inline buttons,
deposit actions, and administrator management keyboards.
"""
from typing import Optional
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from indo_selling_bot.config import settings
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem

CHANNEL_VERIFICATION_TEXT = (
    "Oii Chanel join kr chup Chap 🫵\n"
    "================"
)

BUY_INDO_NOTIFY_TEXT = (
    "cookie login Indo igs \n\n"
    "Note : this is cooke login igs . YOU Recive igs in email:pass:username:cookie:time format .Our Igs have pre Suspend gurente ."
)

def get_buy_indo_notify_keyboard() -> InlineKeyboardMarkup:
    """
    Inline keyboard after clicking Buy Indo:
    - buy  igs (success style)
    - « Back to Main Menu (danger style)
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="buy  igs", callback_data="open_buy_categories", style="success")],
        [InlineKeyboardButton(text="« Back to Main Menu", callback_data="back_to_main", style="danger")]
    ])

def get_channel_verification_keyboard() -> InlineKeyboardMarkup:
    """
    Inline keyboard for channel verification:
    - Join Chanel (green/success style)
    - Verify (blue/primary style)
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Join Chanel", url=settings.force_channel_url, style="success"),
            InlineKeyboardButton(text="Verify", callback_data="verify_channel", style="primary"),
        ]
    ])

def get_start_welcome_keyboard() -> InlineKeyboardMarkup:
    """
    Main welcome keyboard on /start:
    - 🛒 Buy Indo (green/success)
    - 📩 Outlook (blue/primary)
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛒 Buy Indo", callback_data="back_to_buy_indo", style="success"),
            InlineKeyboardButton(text="📩 Outlook", callback_data="outlook_home", style="primary"),
        ]
    ])

def get_main_reply_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """
    Bottom persistent navigation bar.
    Contains customer buttons, plus 1 administrator-only button if caller is admin.
    1. 🛒 Buy Indo
    2. 📩 Outlook (placed at the side of Buy Indo)
    3. 📦 Total Stock
    4. 💰 Add Fund
    5. 👛 My Wallet
    6. 🛡️ Admin Panel (only if is_admin is True)
    """
    keyboard = [
        [KeyboardButton(text="🛒 Buy Indo"), KeyboardButton(text="📩 Outlook")],
        [KeyboardButton(text="📦 Total Stock"), KeyboardButton(text="💰 Add Fund")],
        [KeyboardButton(text="👛 My Wallet")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text="🛡️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=False)

def get_available_buy_categories_keyboard(available_categories: list[dict]) -> InlineKeyboardMarkup:
    """
    Display ONLY categories with genuine available stock.
    Hides empty categories.
    Shows green indicator and '1 day old' / 'N days old' format.
    """
    rows = []
    current_row = []
    for cat in available_categories:
        day = cat["day"]
        label = "1 day old" if day == 1 else f"{day} days old"
        current_row.append(
            InlineKeyboardButton(
                text=f"🫟 {label}",
                callback_data=f"buy_day:{day}",
                style="success"
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
            InlineKeyboardButton(text="« Back to Categories", callback_data="open_buy_categories", style="danger"),
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
        InlineKeyboardButton(text="« Back to Age Categories", callback_data="open_buy_categories", style="danger")
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
        [InlineKeyboardButton(text="📩 Outlook Admin Panel", callback_data="admin_outlook_panel")],
        [InlineKeyboardButton(text="🔙 Back to Main Menu", callback_data="admin:back_main")]
    ])

def get_deposit_request_keyboard(deposit_ref: str | None = None) -> InlineKeyboardMarkup:
    """
    Customer actions when viewing active payment request with QR code:
    Shows ONLY the Back button.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="cancel_payment", style="danger")]
    ])

def get_truex_payment_keyboard(txg_order_id: str, app_url: str | None = None) -> InlineKeyboardMarkup:
    """
    Inline keyboard displayed with dynamic TrueXGateway QR code:
    - 🔄 Check Payment Status
    - 🌐 Pay via UPI / Web (only if http/https url)
    - ❌ Cancel Payment
    """
    rows = [
        [InlineKeyboardButton(text="🔄 Check Payment Status", callback_data=f"txg_chk:{txg_order_id}")]
    ]
    if app_url and (app_url.startswith("http://") or app_url.startswith("https://")):
        rows.append([InlineKeyboardButton(text="🌐 Pay via UPI / Web", url=app_url)])
    rows.append([InlineKeyboardButton(text="❌ Cancel Payment", callback_data=f"txg_cnc:{txg_order_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

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


# ==========================================
# OUTLOOK KEYBOARDS
# ==========================================

def get_outlook_accounts_keyboard(
    accounts: list[dict],
    current_page: int = 1,
    total_pages: int = 1,
    stock_count: int = 0,
    is_admin: bool = False
) -> InlineKeyboardMarkup:
    """
    Screen 1: Lists user's purchased Outlook accounts, buy button, and back button.
    """
    rows = []
    # Account buttons
    for acc in accounts:
        email = acc["email"]
        rows.append([
            InlineKeyboardButton(
                text=f"📥 {email}",
                callback_data=f"acc_view:{acc['id']}:1",
                style="primary"
            )
        ])

    # Optional pagination
    if total_pages > 1:
        nav = []
        if current_page > 1:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"acc_page:{current_page - 1}"))
        else:
            nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        nav.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"acc_page:{current_page + 1}"))
        else:
            nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        rows.append(nav)

    # Buy Button (Green)
    buy_text = f"🛒 Buy Outlook — ₹3 (Stock: {stock_count})" if stock_count > 0 else "🛒 Buy Outlook — ₹3 (Out of Stock)"
    rows.append([
        InlineKeyboardButton(
            text=buy_text,
            callback_data="buy_confirm",
            style="success"
        )
    ])

    if is_admin:
        rows.append([
            InlineKeyboardButton(
                text="👑 Outlook Admin Panel",
                callback_data="admin_outlook_panel",
                style="primary"
            )
        ])

    # Back to Main Menu
    rows.append([
        InlineKeyboardButton(
            text="« Back to Main Menu",
            callback_data="back_to_main",
            style="primary"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_outlook_admin_keyboard() -> InlineKeyboardMarkup:
    """Admin panel keyboard for Outlook management."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add Outlook Stock", callback_data="admin_add_stock_prompt", style="success"),
            InlineKeyboardButton(text="💳 Add User Balance", callback_data="admin_add_balance_prompt", style="primary"),
        ],
        [
            InlineKeyboardButton(text="📊 Outlook Stock Overview", callback_data="admin_view_outlook_stock", style="primary"),
        ],
        [
            InlineKeyboardButton(text="« Back to Outlook Store", callback_data="outlook_home", style="primary"),
            InlineKeyboardButton(text="🏠 Bot Admin Panel", callback_data="admin:back_main", style="primary"),
        ]
    ])


def get_outlook_buy_confirm_keyboard(can_afford: bool = True) -> InlineKeyboardMarkup:
    """Confirmation modal before purchasing Outlook."""
    rows = []
    if can_afford:
        rows.append([
            InlineKeyboardButton(
                text="✅ Confirm & Buy (₹3)",
                callback_data="buy_execute",
                style="success"
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                text="💰 Add Fund",
                callback_data="goto_add_fund",
                style="primary"
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Back",
            callback_data="outlook_home",
            style="primary"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_outlook_purchase_success_keyboard(account_id: int) -> InlineKeyboardMarkup:
    """Keyboard right after successfully purchasing an Outlook email."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="📩 Open Inbox",
                callback_data=f"acc_view:{account_id}:1",
                style="success"
            )
        ],
        [
            InlineKeyboardButton(
                text="📁 My Accounts",
                callback_data="outlook_home",
                style="primary"
            )
        ]
    ])


def get_outlook_inbox_keyboard(
    account_id: int,
    start_num: int,
    items_count: int,
    current_page: int,
    total_pages: int
) -> InlineKeyboardMarkup:
    """
    Screen 2: Inbox numbers, pagination, refresh, back, and remove/restock.
    """
    rows = []

    # Row 1: up to 5 number buttons
    row1_count = min(items_count, 5)
    if row1_count > 0:
        rows.append([
            InlineKeyboardButton(
                text=str(start_num + i),
                callback_data=f"msg_read:{account_id}:{start_num + i - 1}",
                style="primary"
            )
            for i in range(row1_count)
        ])

    # Row 2: if more than 5 on this page
    if items_count > 5:
        rows.append([
            InlineKeyboardButton(
                text=str(start_num + i),
                callback_data=f"msg_read:{account_id}:{start_num + i - 1}",
                style="primary"
            )
            for i in range(5, items_count)
        ])

    # Pagination row
    if total_pages > 1:
        nav = []
        if current_page > 1:
            nav.append(InlineKeyboardButton(text="◀️", callback_data=f"inbox_p:{account_id}:{current_page - 1}"))
        else:
            nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        nav.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
        if current_page < total_pages:
            nav.append(InlineKeyboardButton(text="▶️", callback_data=f"inbox_p:{account_id}:{current_page + 1}"))
        else:
            nav.append(InlineKeyboardButton(text=" ", callback_data="noop"))
        rows.append(nav)

    # Refresh (Green) + Back (Blue)
    rows.append([
        InlineKeyboardButton(
            text="🔄",
            callback_data=f"acc_refresh:{account_id}:{current_page}",
            style="success"
        ),
        InlineKeyboardButton(
            text="⬅️ Back",
            callback_data="outlook_home",
            style="primary"
        )
    ])

    # Remove & Restock button (Danger)
    rows.append([
        InlineKeyboardButton(
            text="🗑️ Remove & Restock",
            callback_data=f"acc_del_ask:{account_id}",
            style="danger"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_outlook_full_email_keyboard(account_id: int, code: Optional[str] = None) -> InlineKeyboardMarkup:
    """Screen 3: Full email view with back button and optional code indicator."""
    rows = []
    if code and code != "------":
        rows.append([
            InlineKeyboardButton(
                text=f"🔑 Code: {code}",
                callback_data="noop",
                style="success"
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Back to Inbox",
            callback_data=f"acc_view:{account_id}:1",
            style="primary"
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_outlook_delete_confirm_keyboard(account_id: int) -> InlineKeyboardMarkup:
    """Confirmation before removing and restocking an email."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🗑️ Confirm Remove & Restock",
                callback_data=f"acc_del_do:{account_id}",
                style="danger"
            )
        ],
        [
            InlineKeyboardButton(
                text="❌ Cancel",
                callback_data=f"acc_view:{account_id}:1",
                style="primary"
            )
        ]
    ])

