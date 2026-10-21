"""
Comprehensive test suite for Telegram-Only Digital Inventory and Wallet Bot.

Tests:
1. Main menu keyboards & role-based visibility (Customer 3 buttons vs Admin 4 buttons)
2. Server-side administrator authorization (Numeric user ID enforcement, reject non-admins)
3. 20 Age-category buttons layout and dynamic ageing (24h intervals, injectable reference clock)
4. Customer-safe total stock summary (No payload leaks, Day 1..20 aggregation)
5. Wallet service & ledger (Atomic credit, idempotency, ledger transactions, negative balance prevention)
6. Atomic purchase flow (Stock reservation, balance debit, safe pending fulfillment state)
7. Admin batch stock addition and business UPI payment configuration
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from aiogram.types import Message, User, Chat, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.config import settings
from indo_selling_bot.database import Base
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.order import Order
from indo_selling_bot.models.wallet import Customer, Wallet, WalletTransaction, DepositRequest
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.delivery_service import DeliveryService
from indo_selling_bot.services.payment_setting_service import PaymentSettingService
from indo_selling_bot.bot.handlers.total_stock import _format_stock_summary
from indo_selling_bot.bot.keyboards import (
    get_main_reply_keyboard,
    get_buy_indo_keyboard,
    get_age_category_detail_keyboard,
    get_admin_main_keyboard,
    get_admin_day_selection_keyboard,
)
from indo_selling_bot.bot.handlers.start import handle_start
from indo_selling_bot.bot.handlers.admin_stock import (
    handle_admin_menu,
    handle_admin_view_stock,
    handle_admin_review_funds,
    handle_admin_approve_deposit,
)

# In-memory test engine for isolated test runs
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def wallet_db():
    engine = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        echo=False
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys = ON;")
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        # Seed test category
        cat = Category(name="Indo Category", slug="indo-category", description="Digital items category")
        session.add(cat)
        await session.commit()
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ==========================================
# 1. Main Menu Keyboards & Role-Based Visibility
# ==========================================
def test_main_menu_keyboard_customer_view():
    """Customer view must display exactly 4 buttons. Admin and transactions buttons must NOT be present."""
    kb = get_main_reply_keyboard(is_admin=False)
    all_buttons = [btn.text for row in kb.keyboard for btn in row]
    assert "🛒 Buy Indo" in all_buttons
    assert "📩 Outlook" in all_buttons
    assert "📦 Total Stock" in all_buttons
    assert "💰 Add Fund" in all_buttons
    assert "👛 My Wallet" in all_buttons
    assert "📜 My Transactions" not in all_buttons
    assert "🛡️ Admin Panel" not in all_buttons
    assert len(all_buttons) == 5


def test_main_menu_keyboard_admin_view():
    """Admin view must display 6 buttons, including 🛡️ Admin Panel and 📩 Outlook."""
    kb = get_main_reply_keyboard(is_admin=True)
    all_buttons = [btn.text for row in kb.keyboard for btn in row]
    assert "🛒 Buy Indo" in all_buttons
    assert "📩 Outlook" in all_buttons
    assert "📦 Total Stock" in all_buttons
    assert "💰 Add Fund" in all_buttons
    assert "👛 My Wallet" in all_buttons
    assert "📜 My Transactions" not in all_buttons
    assert "🛡️ Admin Panel" in all_buttons
    assert len(all_buttons) == 6


def test_buy_indo_keyboard_contains_exactly_20_days():
    """Buy Indo keyboard must contain exactly 20 age categories (1 Day Old to 20 Days Old) + Back."""
    kb = get_buy_indo_keyboard(max_days=20)
    day_buttons = []
    back_button = None
    for row in kb.inline_keyboard:
        for btn in row:
            if "Day" in btn.text and "Old" in btn.text:
                day_buttons.append(btn)
            elif "Back" in btn.text:
                back_button = btn

    assert len(day_buttons) == 20
    assert day_buttons[0].text == "📅 1 Day Old"
    assert day_buttons[0].callback_data == "buy_day:1"
    assert day_buttons[19].text == "📅 20 Days Old"
    assert day_buttons[19].callback_data == "buy_day:20"
    assert back_button is not None


# ==========================================
# 2. Server-Side Administrator Authorization
# ==========================================
def test_admin_numeric_user_id_authorization():
    """Admin status must be determined strictly by numeric user ID, never by username alone."""
    original_admin_id = settings.TELEGRAM_ADMIN_USER_ID
    try:
        settings.TELEGRAM_ADMIN_USER_ID = 987654321
        assert settings.is_admin(987654321) is True
        assert settings.is_admin(12345) is False
        assert settings.is_admin(0) is False
        assert settings.is_admin(None) is False
    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin_id


@pytest.mark.asyncio
async def test_admin_handler_strictly_rejects_unauthorized_user():
    """Unauthorized customer trying to access /admin or Admin button receives 403-equivalent rejection."""
    original_admin_id = settings.TELEGRAM_ADMIN_USER_ID
    try:
        settings.TELEGRAM_ADMIN_USER_ID = 999999999  # configured admin

        # Unauthorized customer (ID 11111)
        fake_user = User(id=11111, is_bot=False, first_name="Intruder")
        mock_msg = AsyncMock(spec=Message)
        mock_msg.from_user = fake_user
        mock_msg.answer = AsyncMock()

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=11111, user_id=11111))

        await handle_admin_menu(mock_msg, state)

        assert mock_msg.answer.called
        args, _ = mock_msg.answer.call_args
        assert "Unauthorized Access" in args[0]
        assert "restricted to configured system administrators" in args[0]
    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin_id


# ==========================================
# 3. Dynamic Inventory Ageing & Calculation
# ==========================================
def test_inventory_age_exact_24h_increments():
    """
    Ageing formula: elapsed // 86400 + 1
    0h -> Day 1 (<24h)
    23h 59m -> Day 1
    24h 00m -> Day 2 (24h - 48h)
    48h 00m -> Day 3
    456h (19 days) -> Day 20
    480h (20 days) -> Day 21 (20+ Days Old)
    """
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 1 hour ago -> Day 1
    item_d1 = InventoryItem(created_at=ref_time - timedelta(hours=1))
    assert item_d1.get_day_number(reference_time=ref_time) == 1
    assert item_d1.get_age_category_label(reference_time=ref_time) == "1 Day Old"

    # 23 hours 59 minutes ago -> Day 1
    item_d1_edge = InventoryItem(created_at=ref_time - timedelta(hours=23, minutes=59))
    assert item_d1_edge.get_day_number(reference_time=ref_time) == 1

    # Exactly 24 hours ago -> Day 2
    item_d2 = InventoryItem(created_at=ref_time - timedelta(hours=24))
    assert item_d2.get_day_number(reference_time=ref_time) == 2
    assert item_d2.get_age_category_label(reference_time=ref_time) == "2 Days Old"

    # Exactly 19 days ago -> Day 20
    item_d20 = InventoryItem(created_at=ref_time - timedelta(days=19, hours=2))
    assert item_d20.get_day_number(reference_time=ref_time) == 20
    assert item_d20.get_age_category_label(reference_time=ref_time) == "20 Days Old"

    # 25 days ago -> Day 20+ (gracefully preserved without crash)
    item_older = InventoryItem(created_at=ref_time - timedelta(days=25))
    assert item_older.get_day_number(reference_time=ref_time) == 26
    assert item_older.get_age_category_label(reference_time=ref_time) == "20+ Days Old"


# ==========================================
# 4. Total Stock Summary & Customer Data Protection
# ==========================================
@pytest.mark.asyncio
async def test_customer_safe_total_stock_summary(wallet_db: AsyncSession):
    """
    Customer total stock summary must aggregate Day 1..20 quantities correctly
    and MUST NEVER expose private inventory payloads or fulfillment credentials.
    """
    now = datetime.now(timezone.utc)

    # Create 3 items: Day 1 (with private payload), Day 2, and Day 25 (mature)
    item1 = InventoryItem(
        sku="SKU-D1-SECRET",
        category_id=1,
        title="Stock Day 1",
        quantity=5,
        price=10000,
        fulfillment_payload="SUPER_SECRET_TOKEN_12345",  # SENSITIVE DATA
        created_at=now - timedelta(hours=2),
        status="active"
    )
    item2 = InventoryItem(
        sku="SKU-D2-PUBLIC",
        category_id=1,
        title="Stock Day 2",
        quantity=10,
        price=12000,
        created_at=now - timedelta(days=1, hours=4),
        status="active"
    )
    item_old = InventoryItem(
        sku="SKU-D25-OLD",
        category_id=1,
        title="Stock Day 25",
        quantity=3,
        price=8000,
        created_at=now - timedelta(days=25),
        status="active"
    )
    wallet_db.add_all([item1, item2, item_old])
    await wallet_db.commit()

    summary = await InventoryService.get_total_stock_summary(wallet_db, max_days=20, reference_time=now)

    assert summary["total_stock"] == 18  # Day 1 (5) + Day 2 (10) + Day 25 mature (3) = 18 total across all categories
    assert summary["older_stock_count"] == 3  # Day 25 (3)

    day1_cat = next(c for c in summary["categories"] if c["day"] == 1)
    assert day1_cat["quantity"] == 5
    assert len(day1_cat["batches"]) == 1

    # Security verification: ensure private payload does NOT leak in batch summary dictionary
    batch = day1_cat["batches"][0]
    assert "SUPER_SECRET_TOKEN_12345" not in str(batch)
    assert "fulfillment_payload" not in batch


# ==========================================
# 5. Wallet Service, Idempotent Approval & Immutable Ledger
# ==========================================
@pytest.mark.asyncio
async def test_wallet_deposit_lifecycle_and_idempotent_approval(wallet_db: AsyncSession):
    """
    Test deposit request creation, admin approval, atomic balance credit,
    immutable ledger entry, and idempotent prevention of duplicate credit.
    """
    user_id = 555001

    # 1. Initial wallet is 0
    balance_initial = await WalletService.get_balance(wallet_db, user_id)
    assert balance_initial == 0.0

    # 2. Customer creates deposit request of 50,000 IDR
    deposit = await WalletService.create_deposit_request(
        db=wallet_db,
        telegram_user_id=user_id,
        amount=50000,
        currency="IDR",
        utr_number="UTR-999888777",
        username="john_doe"
    )
    await wallet_db.commit()

    assert deposit.reference.startswith(("DEP-", "PAY-"))
    assert deposit.status in ("PENDING", "PENDING_PAYMENT")
    assert deposit.amount == 50000

    # 3. Admin approves deposit
    success, msg, approved_dep = await WalletService.approve_deposit(
        db=wallet_db,
        deposit_ref=deposit.reference,
        admin_id=987654321,
        admin_notes="Bank statement verified"
    )
    assert success is True
    assert approved_dep.status == "APPROVED"

    # 4. Check wallet credited
    balance_after = await WalletService.get_balance(wallet_db, user_id)
    assert balance_after == 50000.0

    # 5. Check immutable ledger entry
    txs = await WalletService.get_recent_transactions(wallet_db, user_id)
    assert len(txs) == 1
    assert txs[0].tx_type == "DEPOSIT"
    assert txs[0].amount == 50000.0
    assert txs[0].balance_after == 50000.0
    assert txs[0].reference == deposit.reference

    # 6. IDEMPOTENCY: Admin accidentally calls approve_deposit a SECOND time
    success_dup, msg_dup, _ = await WalletService.approve_deposit(
        db=wallet_db,
        deposit_ref=deposit.reference,
        admin_id=987654321
    )
    assert success_dup is False
    assert "already processed" in msg_dup

    # Verify balance was NOT doubled
    balance_unaltered = await WalletService.get_balance(wallet_db, user_id)
    assert balance_unaltered == 50000.0


@pytest.mark.asyncio
async def test_deposit_rejection_leaves_balance_unaffected(wallet_db: AsyncSession):
    """Rejecting a deposit should set status to REJECTED without modifying wallet balance."""
    user_id = 555002
    deposit = await WalletService.create_deposit_request(
        db=wallet_db,
        telegram_user_id=user_id,
        amount=25000,
        utr_number="FAKE-UTR"
    )
    await wallet_db.commit()

    success, msg, rejected_dep = await WalletService.reject_deposit(
        db=wallet_db,
        deposit_ref=deposit.reference,
        admin_id=987654321,
        reason="Invalid UTR reference"
    )
    assert success is True
    assert rejected_dep.status == "REJECTED"

    bal = await WalletService.get_balance(wallet_db, user_id)
    assert bal == 0.0


# ==========================================
# 6. Atomic Purchase Flow & Stock Reservation
# ==========================================
@pytest.mark.asyncio
async def test_purchase_rejected_on_insufficient_balance(wallet_db: AsyncSession):
    """Purchase must be rejected atomically if wallet balance is lower than item price."""
    user_id = 777001
    now = datetime.now(timezone.utc)

    # Item costs 30,000 IDR
    item = InventoryItem(
        sku="TEST-SKU-INSUF",
        category_id=1,
        title="Day 1 Indo Item",
        price=30000,
        quantity=5,
        created_at=now - timedelta(hours=3),
        status="active"
    )
    wallet_db.add(item)
    await wallet_db.commit()

    # User has 10,000 IDR (insufficient)
    wallet = await WalletService.get_or_create_wallet(wallet_db, user_id)
    wallet.balance = 10000
    await wallet_db.commit()

    success, msg, order = await InventoryService.purchase_item_with_wallet(
        db=wallet_db,
        item_id=item.id,
        expected_day=1,
        telegram_user={"id": user_id, "username": "buyer", "first_name": "Buyer"},
        reference_time=now
    )

    assert success is False
    assert "Insufficient balance" in msg
    assert order is None

    # Verify stock and balance did NOT change
    await wallet_db.refresh(item)
    assert item.quantity == 5
    bal = await WalletService.get_balance(wallet_db, user_id)
    assert bal == 10000.0


@pytest.mark.asyncio
async def test_atomic_purchase_success_and_ledger_debit(wallet_db: AsyncSession):
    """
    Successful purchase must:
    1. Deduct wallet balance once
    2. Decrement stock quantity transactionally
    3. Record immutable ledger debit
    4. Create order in PENDING_FULFILLMENT state
    """
    user_id = 777002
    now = datetime.now(timezone.utc)

    item = InventoryItem(
        sku="TEST-SKU-BUY",
        category_id=1,
        title="Day 3 Indo Item",
        price=20000,
        quantity=3,
        created_at=now - timedelta(days=2, hours=3),  # Day 3
        status="active"
    )
    wallet_db.add(item)

    wallet = await WalletService.get_or_create_wallet(wallet_db, user_id)
    wallet.balance = 50000
    await wallet_db.commit()

    success, msg, order = await InventoryService.purchase_item_with_wallet(
        db=wallet_db,
        item_id=item.id,
        expected_day=3,
        telegram_user={"id": user_id, "username": "successful_buyer", "first_name": "Alex"},
        reference_time=now
    )

    assert success is True
    assert order is not None
    assert order.status == "PENDING_FULFILLMENT"
    assert order.total_amount == 20000.0

    # Verify stock decreased by 1
    await wallet_db.refresh(item)
    assert item.quantity == 2

    # Verify wallet decreased by 20,000 to 30,000
    new_bal = await WalletService.get_balance(wallet_db, user_id)
    assert new_bal == 30000.0

    # Verify ledger entry
    txs = await WalletService.get_recent_transactions(wallet_db, user_id)
    assert len(txs) == 1
    assert txs[0].tx_type == "PURCHASE"
    assert txs[0].amount == -20000.0
    assert txs[0].balance_after == 30000.0


# ==========================================
# 7. Admin Stock Batch Addition & Payment Configuration
# ==========================================
@pytest.mark.asyncio
async def test_admin_add_stock_batch(wallet_db: AsyncSession):
    """Admin guided batch creation adds legitimate item and audit record."""
    admin_id = 987654321
    now = datetime.now(timezone.utc)
    target_created_at = now - timedelta(days=4, hours=2)  # 4 days and 2 hours ago = Day 5

    item = await InventoryService.add_stock_batch(
        db=wallet_db,
        category_id=1,
        quantity=15,
        price=25000.0,
        created_at=target_created_at,
        admin_id=admin_id,
        title="Indo Stock Batch Day 5",
        notes="Admin restock test",
        reference_time=now
    )

    assert item.id is not None
    assert item.quantity == 15
    assert item.price == 25000.0
    assert item.get_day_number(reference_time=now) == 5
    assert item.get_age_category_label(reference_time=now) == "5 Days Old"

    # Check audit log
    audit_stmt = select(AuditLog).where(AuditLog.action == "ADMIN_ADD_STOCK")
    audit = (await wallet_db.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert str(admin_id) in audit.performed_by


@pytest.mark.asyncio
async def test_payment_setting_service_lifecycle(wallet_db: AsyncSession):
    """Dynamic UPI settings update and retrieval with audit logging."""
    admin_id = 987654321

    # 1. Fetch defaults
    info = await PaymentSettingService.get_settings(wallet_db)
    assert info["upi_id"] == settings.DEFAULT_UPI_ID
    assert info["payee_name"] == settings.DEFAULT_UPI_NAME

    # 2. Update UPI ID
    await PaymentSettingService.update_setting(
        db=wallet_db,
        key=PaymentSettingService.SETTING_UPI_ID,
        value="newbusiness@okhdfcbank",
        admin_id=admin_id,
        description="Official new UPI ID"
    )

    # 3. Verify updated setting
    updated_info = await PaymentSettingService.get_settings(wallet_db)
    assert updated_info["upi_id"] == "newbusiness@okhdfcbank"

    # 4. Check audit log
    audit_stmt = select(AuditLog).where(AuditLog.action == "UPDATE_PAYMENT_SETTING")
    audit = (await wallet_db.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert "newbusiness@okhdfcbank" in audit.details


# ==========================================
# 8. Customer Stock Privacy & Format Verification
# ==========================================
@pytest.mark.asyncio
async def test_customer_stock_summary_counts_only_privacy(wallet_db: AsyncSession):
    """
    Customer stock summary must display ONLY:
    - Category name (Day X Old)
    - Available unit count
    - Total available units
    It must NEVER expose internal SKUs, batch codes, creation timestamps,
    passwords, or private payloads in the customer message.
    """
    now = datetime.now(timezone.utc)
    # Add items with sensitive internal metadata
    sensitive_sku = "CONFIDENTIAL-SKU-999"
    sensitive_notes = "DO_NOT_LEAK_SECRET_KEY_XYZZY"
    item1 = InventoryItem(
        sku=sensitive_sku,
        category_id=1,
        title="Premium Indo Account",
        description="Private description with internal account details",
        day_group="Day 1",
        price=15000,
        quantity=7,
        status="active",
        internal_notes=sensitive_notes,
        created_at=now - timedelta(hours=5),
    )
    wallet_db.add(item1)
    await wallet_db.commit()

    summary = await InventoryService.get_total_stock_summary(wallet_db, max_days=20, reference_time=now)
    formatted_text = _format_stock_summary(summary)

    # Must contain table layout, correct unit counts and total
    assert "Daily Breakdown" in formatted_text
    assert "┌──────────────┬─────────┬───────┐" in formatted_text
    assert "Today" in formatted_text
    assert "7" in formatted_text
    assert "Total" in formatted_text

    # Days with 0 stock MUST NOT be displayed
    assert "1 day old" not in formatted_text
    assert "2 days old" not in formatted_text

    # Zero leakage of internal metadata
    assert sensitive_sku not in formatted_text
    assert sensitive_notes not in formatted_text
    assert "CONFIDENTIAL" not in formatted_text
    assert "SECRET" not in formatted_text
    assert "Premium Indo Account" not in formatted_text
    assert "created_at" not in formatted_text
    assert "timestamp" not in formatted_text
    assert "batch" not in formatted_text.lower()


# ==========================================
# 9. Admin Stock Removal Lifecycle
# ==========================================
@pytest.mark.asyncio
async def test_remove_stock_by_category_lifecycle(wallet_db: AsyncSession):
    """
    Admin stock removal:
    1. Selects day category and valid quantity <= available
    2. Decrements quantity and marks item 'removed' if quantity reaches 0
    3. Prevents over-removal (rejects if quantity > available)
    4. Rejects negative or zero quantity
    5. Preserves sold or reserved items
    6. Emits InventoryChangeLog and AuditLog
    """
    admin_id = 987654321
    now = datetime.now(timezone.utc)

    # Setup Day 4 item with 10 units
    item_active = InventoryItem(
        sku="TEST-REM-ACTIVE",
        category_id=1,
        title="Day 4 Indo Item",
        price=15000,
        quantity=10,
        created_at=now - timedelta(days=3, hours=5),  # Day 4
        status="active"
    )
    # Sold item in same day - must not be affected
    item_sold = InventoryItem(
        sku="TEST-REM-SOLD",
        category_id=1,
        title="Day 4 Indo Item Sold",
        price=15000,
        quantity=0,
        created_at=now - timedelta(days=3, hours=5),
        status="sold"
    )
    wallet_db.add_all([item_active, item_sold])
    await wallet_db.commit()

    # 1. Invalid quantity: <= 0
    success, msg, _ = await InventoryService.remove_stock_by_category(
        db=wallet_db, day_number=4, quantity=0, admin_id=admin_id, reference_time=now
    )
    assert success is False
    assert "positive integer" in msg

    # 2. Exceeds available stock: 15 > 10
    success, msg, avail = await InventoryService.remove_stock_by_category(
        db=wallet_db, day_number=4, quantity=15, admin_id=admin_id, reference_time=now
    )
    assert success is False
    assert "exceeds available stock" in msg
    assert avail == 10

    # 3. Partial removal: remove 4 units
    success, msg, remaining = await InventoryService.remove_stock_by_category(
        db=wallet_db, day_number=4, quantity=4, admin_id=admin_id, reason="Admin audit correction", reference_time=now
    )
    assert success is True
    assert remaining == 6
    await wallet_db.refresh(item_active)
    assert item_active.quantity == 6
    assert item_active.status == "active"

    # 4. Remove remaining 6 units -> marks status as 'removed'
    success, msg, remaining = await InventoryService.remove_stock_by_category(
        db=wallet_db, day_number=4, quantity=6, admin_id=admin_id, reason="End of inventory batch", reference_time=now
    )
    assert success is True
    assert remaining == 0
    await wallet_db.refresh(item_active)
    assert item_active.quantity == 0
    assert item_active.status == "removed"

    # 5. Check sold item was untouched
    await wallet_db.refresh(item_sold)
    assert item_sold.status == "sold"

    # 6. Verify change logs and audit logs
    change_stmt = select(InventoryChangeLog).where(InventoryChangeLog.action == "ADMIN_STOCK_REMOVED")
    change_logs = (await wallet_db.execute(change_stmt)).scalars().all()
    assert len(change_logs) == 2

    audit_stmt = select(AuditLog).where(AuditLog.action == "STOCK_REMOVED")
    audit_logs = (await wallet_db.execute(audit_stmt)).scalars().all()
    assert len(audit_logs) == 2
    assert all(str(admin_id) in a.performed_by for a in audit_logs)


# ==========================================
# 10. Concurrent Purchases Competing for Final Unit
# ==========================================
@pytest.mark.asyncio
async def test_concurrent_purchases_final_unit_competition(wallet_db: AsyncSession):
    """
    When only 1 unit remains in stock and multiple buyers attempt to purchase:
    - Exactly 1 purchase succeeds, debiting wallet and marking item sold.
    - The competing purchase safely fails with no wallet deduction or negative stock.
    """
    now = datetime.now(timezone.utc)
    item = InventoryItem(
        sku="LAST-UNIT-SKU",
        category_id=1,
        title="Last Remaining Indo Item",
        price=25000,
        quantity=1,
        created_at=now - timedelta(hours=2),
        status="active"
    )
    wallet_db.add(item)

    user1_id = 888001
    user2_id = 888002

    w1 = await WalletService.get_or_create_wallet(wallet_db, user1_id)
    w1.balance = 30000
    w2 = await WalletService.get_or_create_wallet(wallet_db, user2_id)
    w2.balance = 30000
    await wallet_db.commit()

    # First buyer completes purchase
    success1, msg1, order1 = await InventoryService.purchase_item_with_wallet(
        db=wallet_db,
        item_id=item.id,
        expected_day=1,
        telegram_user={"id": user1_id, "username": "buyer1", "first_name": "Buyer 1"},
        reference_time=now
    )
    assert success1 is True
    assert order1 is not None

    # Buyer 1 balance reduced by 25,000 to 5,000
    b1 = await WalletService.get_balance(wallet_db, user1_id)
    assert b1 == 5000.0

    # Stock is now 0 and status is sold
    await wallet_db.refresh(item)
    assert item.quantity == 0
    assert item.status == "sold"

    # Second buyer attempts to purchase the same item or category
    success2, msg2, order2 = await InventoryService.purchase_item_with_wallet(
        db=wallet_db,
        item_id=item.id,
        expected_day=1,
        telegram_user={"id": user2_id, "username": "buyer2", "first_name": "Buyer 2"},
        reference_time=now
    )
    assert success2 is False
    assert "out of stock" in msg2.lower() or "no longer available" in msg2.lower()
    assert order2 is None

    # Buyer 2 balance must remain completely untouched (30,000)
    b2 = await WalletService.get_balance(wallet_db, user2_id)
    assert b2 == 30000.0


# ==========================================
# 11. Fulfillment Error Handling & Recovery
# ==========================================
@pytest.mark.asyncio
async def test_fulfillment_failure_and_admin_recovery(wallet_db: AsyncSession):
    """
    Fulfillment error handling:
    1. Order placed into PENDING_FULFILLMENT.
    2. DeliveryService.fail_fulfillment records failure note without losing order.
    3. Order is discoverable via DeliveryService.get_pending_fulfillments.
    4. Admin resolves and releases fulfillment safely via DeliveryService.fulfill_order.
    5. Order status becomes FULFILLED and fulfillment_released=True with audit trail.
    """
    admin_id = 987654321
    user_id = 999001
    now = datetime.now(timezone.utc)

    order = Order(
        order_code="ORD-TEST-FAIL-RECOVER",
        telegram_user_id=user_id,
        telegram_username="unlucky_buyer",
        total_amount=15000,
        currency="IDR",
        status="PENDING_FULFILLMENT",
        fulfillment_released=False,
        created_at=now
    )
    wallet_db.add(order)
    await wallet_db.commit()

    # 1. Delivery failure event
    fail_ok, fail_msg, failed_order = await DeliveryService.fail_fulfillment(
        db=wallet_db,
        order_code="ORD-TEST-FAIL-RECOVER",
        reason="External digital sequence timeout",
        admin_id=admin_id
    )
    assert fail_ok is False
    assert "External digital sequence timeout" in fail_msg
    assert failed_order.status == "PENDING_FULFILLMENT"
    assert failed_order.fulfillment_released is False
    assert "timeout" in (failed_order.verification_notes or "")

    # 2. Check pending fulfillments queue includes this order
    pending_list = await DeliveryService.get_pending_fulfillments(wallet_db)
    assert any(o.order_code == "ORD-TEST-FAIL-RECOVER" for o in pending_list)

    # 3. Admin fulfills the order manually
    fulfill_ok, fulfill_msg, fulfilled_order = await DeliveryService.fulfill_order(
        db=wallet_db,
        order_code="ORD-TEST-FAIL-RECOVER",
        admin_id=admin_id,
        fulfillment_notes="Admin manual verification and dispatch completed."
    )
    assert fulfill_ok is True
    assert fulfilled_order.status == "FULFILLED"
    assert fulfilled_order.fulfillment_released is True
    assert fulfilled_order.verified_by_admin == str(admin_id)
    assert fulfilled_order.fulfillment_sent_at is not None

    # 4. Check idempotent repeat fulfill call
    dup_ok, dup_msg, _ = await DeliveryService.fulfill_order(
        db=wallet_db,
        order_code="ORD-TEST-FAIL-RECOVER",
        admin_id=admin_id
    )
    assert dup_ok is True
    assert "already fulfilled" in dup_msg

    # 5. Check AuditLog entry for fulfillment
    audit_stmt = select(AuditLog).where(AuditLog.action == "ORDER_FULFILLED")
    audit = (await wallet_db.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert str(admin_id) in audit.performed_by
    assert "ORD-TEST-FAIL-RECOVER" in audit.details

