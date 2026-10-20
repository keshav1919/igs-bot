"""
Comprehensive automated tests for Part 1 - Part 16 Telegram Bot Upgrade:
1. Live stock query: Only available categories appear in Buy Indo, empty categories hidden.
2. Payments: ₹29 rejected, ₹30 accepted, negative/zero rejected.
3. Dynamic UPI QR generation with configured UPI ID and requested amount.
4. Customer payment screenshot workflow: PENDING_PAYMENT -> PENDING_REVIEW -> APPROVED.
5. Screenshot alone does not credit wallet; verified approval credits wallet exactly once.
6. Rejection never credits wallet and records admin reason.
7. Idempotency: Duplicate approval attempts do not double-credit wallet.
8. Customer transaction history and status tracking.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.database import Base
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.models.wallet import Customer, Wallet, WalletTransaction, DepositRequest
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.bot.keyboards import (
    get_main_reply_keyboard,
    get_available_buy_categories_keyboard,
    get_admin_main_keyboard,
)
from indo_selling_bot.bot.handlers.add_fund import generate_upi_qr_bytes

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def upgrade_db():
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
        cat = Category(name="Indo Category", slug="indo-category", description="Digital items category")
        session.add(cat)
        await session.commit()
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ==========================================
# 1. Stock: Only Available Categories in Buy Indo
# ==========================================
@pytest.mark.asyncio
async def test_only_available_categories_appear_in_buy_indo(upgrade_db: AsyncSession):
    """
    Buy Indo must:
    - Display ONLY categories containing at least 1 genuinely available unit.
    - Hide categories with 0 available units.
    - Exclude sold, reserved, removed, or paused inventory.
    """
    now = datetime.now(timezone.utc)

    # Day 1: 5 active units
    item_d1 = InventoryItem(
        sku="SKU-AVAIL-D1",
        category_id=1,
        title="Day 1 Active Item",
        price=25000,
        quantity=5,
        status="active",
        created_at=now - timedelta(hours=3)  # Day 1
    )
    # Day 3: 2 active units
    item_d3 = InventoryItem(
        sku="SKU-AVAIL-D3",
        category_id=1,
        title="Day 3 Active Item",
        price=20000,
        quantity=2,
        status="active",
        created_at=now - timedelta(days=2, hours=3)  # Day 3
    )
    # Day 2: 0 units (sold out) - MUST BE HIDDEN
    item_d2 = InventoryItem(
        sku="SKU-ZERO-D2",
        category_id=1,
        title="Day 2 Sold Item",
        price=22000,
        quantity=0,
        status="active",
        created_at=now - timedelta(days=1, hours=3)  # Day 2
    )
    # Day 4: 10 units but status="removed" - MUST BE HIDDEN
    item_d4_removed = InventoryItem(
        sku="SKU-REM-D4",
        category_id=1,
        title="Day 4 Removed Item",
        price=18000,
        quantity=10,
        status="removed",
        created_at=now - timedelta(days=3, hours=3)
    )
    # Day 5: 5 units but status="paused" - MUST BE HIDDEN
    item_d5_paused = InventoryItem(
        sku="SKU-PAUSED-D5",
        category_id=1,
        title="Day 5 Paused Item",
        price=16000,
        quantity=5,
        status="paused",
        created_at=now - timedelta(days=4, hours=3)
    )

    upgrade_db.add_all([item_d1, item_d3, item_d2, item_d4_removed, item_d5_paused])
    await upgrade_db.commit()

    # Query available categories
    available = await InventoryService.get_available_categories_for_buy(upgrade_db, reference_time=now)

    # Exactly Day 1 and Day 3 should be returned
    days_present = [c["day"] for c in available]
    assert days_present == [1, 3]

    # Verify Day 2, 4, 5 are NOT present
    assert 2 not in days_present
    assert 4 not in days_present
    assert 5 not in days_present

    # Test keyboard generation: only buttons for Day 1 and Day 3
    kb = get_available_buy_categories_keyboard(available)
    button_texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Day 1 Old" in t for t in button_texts)
    assert any("Day 3 Old" in t for t in button_texts)
    assert not any("Day 2 Old" in t for t in button_texts)
    assert not any("Day 4 Old" in t for t in button_texts)


@pytest.mark.asyncio
async def test_all_categories_out_of_stock_empty_behavior(upgrade_db: AsyncSession):
    """When all categories have 0 stock, get_available_categories_for_buy returns empty list."""
    available = await InventoryService.get_available_categories_for_buy(upgrade_db)
    assert available == []


# ==========================================
# 2. Payments: ₹30 Minimum & Validation
# ==========================================
@pytest.mark.asyncio
async def test_minimum_deposit_validation(upgrade_db: AsyncSession):
    """
    Payments requirement:
    - ₹29 and lower must be rejected.
    - ₹30 must be accepted.
    - Zero and negative amounts must be rejected.
    """
    user_id = 1234567

    # ₹29 MUST BE REJECTED
    with pytest.raises(ValueError) as excinfo:
        await WalletService.create_deposit_request(
            db=upgrade_db,
            telegram_user_id=user_id,
            amount=29
        )
    assert "Minimum Add Fund Amount is ₹30" in str(excinfo.value)

    # ₹0 MUST BE REJECTED
    with pytest.raises(ValueError):
        await WalletService.create_deposit_request(
            db=upgrade_db,
            telegram_user_id=user_id,
            amount=0
        )

    # Negative amount MUST BE REJECTED
    with pytest.raises(ValueError):
        await WalletService.create_deposit_request(
            db=upgrade_db,
            telegram_user_id=user_id,
            amount=-100
        )

    # ₹30 MUST BE ACCEPTED
    dep30 = await WalletService.create_deposit_request(
        db=upgrade_db,
        telegram_user_id=user_id,
        amount=30
    )
    assert dep30.amount == 30
    assert dep30.status == "PENDING_PAYMENT"
    assert dep30.reference.startswith(("PAY-", "DEP-"))

    # Higher amount (₹500) MUST BE ACCEPTED
    dep500 = await WalletService.create_deposit_request(
        db=upgrade_db,
        telegram_user_id=user_id,
        amount=500
    )
    assert dep500.amount == 500


# ==========================================
# 3. Dynamic UPI QR Code Generation
# ==========================================
def test_dynamic_upi_qr_generation():
    """Verify QR code is generated dynamically from properly encoded UPI URI."""
    upi_id = "paytm.s1x87m2@pty"
    payee = "Legend Indo Official"
    amount = 50.00
    ref_id = "PAY-999888"

    qr_bytes = generate_upi_qr_bytes(
        upi_id=upi_id,
        payee_name=payee,
        amount=amount,
        ref_id=ref_id
    )

    assert isinstance(qr_bytes, bytes)
    assert len(qr_bytes) > 200
    # PNG signature check: \x89PNG\r\n\x1a\n
    assert qr_bytes.startswith(b"\x89PNG\r\n\x1a\n")


# ==========================================
# 4. Screenshot Submission & Status Transition
# ==========================================
@pytest.mark.asyncio
async def test_screenshot_submission_and_review_workflow(upgrade_db: AsyncSession):
    """
    Screenshot upload:
    - Initial state: PENDING_PAYMENT
    - Screenshot upload changes status to PENDING_REVIEW
    - Screenshot submission ALONE does NOT credit the wallet
    """
    user_id = 789123
    deposit = await WalletService.create_deposit_request(
        db=upgrade_db,
        telegram_user_id=user_id,
        amount=100
    )
    await upgrade_db.commit()
    assert deposit.status == "PENDING_PAYMENT"

    # Customer uploads screenshot
    fake_file_id = "AgACAgUAAxkBAAI_fake_screenshot_file_id"
    ok, msg, updated_dep = await WalletService.attach_screenshot(
        db=upgrade_db,
        deposit_ref=deposit.reference,
        screenshot_file_id=fake_file_id
    )

    assert ok is True
    assert updated_dep.status == "PENDING_REVIEW"
    assert updated_dep.screenshot_file_id == fake_file_id

    # VERIFY WALLET IS NOT CREDITED YET
    bal = await WalletService.get_balance(upgrade_db, user_id)
    assert bal == 0.0


# ==========================================
# 5. Admin Verified Approval and Idempotency
# ==========================================
@pytest.mark.asyncio
async def test_verified_admin_approval_and_idempotency(upgrade_db: AsyncSession):
    """
    Admin verified approval:
    - Atomically updates status to APPROVED
    - Credits customer wallet exactly once
    - Writes immutable ledger transaction
    - Duplicate approval attempts fail and do NOT double-credit
    """
    user_id = 999111
    admin_id = 5344918305

    deposit = await WalletService.create_deposit_request(
        db=upgrade_db,
        telegram_user_id=user_id,
        amount=250
    )
    await WalletService.attach_screenshot(upgrade_db, deposit.reference, "file_xyz")
    await upgrade_db.commit()

    # 1. Admin approves
    ok, msg, approved_dep = await WalletService.approve_deposit(
        db=upgrade_db,
        deposit_ref=deposit.reference,
        admin_id=admin_id,
        admin_notes="Bank statement verified"
    )
    assert ok is True
    assert approved_dep.status == "APPROVED"

    # Verify wallet credited
    bal = await WalletService.get_balance(upgrade_db, user_id)
    assert bal == 250.0

    # Verify immutable ledger entry
    txs = await WalletService.get_recent_transactions(upgrade_db, user_id)
    assert len(txs) == 1
    assert txs[0].tx_type == "DEPOSIT"
    assert txs[0].amount == 250
    assert txs[0].balance_after == 250

    # 2. Duplicate approval attempt MUST FAIL and NOT DOUBLE-CREDIT
    dup_ok, dup_msg, _ = await WalletService.approve_deposit(
        db=upgrade_db,
        deposit_ref=deposit.reference,
        admin_id=admin_id
    )
    assert dup_ok is False
    assert "already processed" in dup_msg.lower()

    # Balance must remain exactly 250.0
    bal_after = await WalletService.get_balance(upgrade_db, user_id)
    assert bal_after == 250.0


# ==========================================
# 6. Admin Rejection Never Credits Wallet
# ==========================================
@pytest.mark.asyncio
async def test_admin_rejection_leaves_balance_zero(upgrade_db: AsyncSession):
    """
    Payment rejection:
    - Status becomes REJECTED
    - Wallet balance is NOT credited
    - Admin reason is recorded
    """
    user_id = 888222
    admin_id = 5344918305

    deposit = await WalletService.create_deposit_request(
        db=upgrade_db,
        telegram_user_id=user_id,
        amount=150
    )
    await upgrade_db.commit()

    ok, msg, rejected_dep = await WalletService.reject_deposit(
        db=upgrade_db,
        deposit_ref=deposit.reference,
        admin_id=admin_id,
        reason="Transaction UTR not found on bank statement"
    )
    assert ok is True
    assert rejected_dep.status == "REJECTED"
    assert "bank statement" in rejected_dep.admin_notes

    # Balance remains 0
    bal = await WalletService.get_balance(upgrade_db, user_id)
    assert bal == 0.0

    # Attempting to approve rejected deposit fails
    app_ok, _, _ = await WalletService.approve_deposit(upgrade_db, deposit.reference, admin_id)
    assert app_ok is False
    assert (await WalletService.get_balance(upgrade_db, user_id)) == 0.0


# ==========================================
# 7. Customer Transactions History
# ==========================================
@pytest.mark.asyncio
async def test_customer_transactions_history_tracking(upgrade_db: AsyncSession):
    """Customer can query recent deposit requests and purchase history."""
    user_id = 777333
    now = datetime.now(timezone.utc)

    # Create 2 deposits
    d1 = await WalletService.create_deposit_request(upgrade_db, user_id, 50)
    d2 = await WalletService.create_deposit_request(upgrade_db, user_id, 100)
    await WalletService.approve_deposit(upgrade_db, d2.reference, 5344918305)

    # Fetch deposit requests for customer
    customer_deposits = await WalletService.get_customer_deposit_requests(upgrade_db, user_id)
    assert len(customer_deposits) == 2
    refs = [d.reference for d in customer_deposits]
    assert d1.reference in refs
    assert d2.reference in refs

    # Make a purchase
    item = InventoryItem(
        sku="TEST-SKU-TXHIST",
        category_id=1,
        title="Day 1 Test Item",
        price=40,
        quantity=3,
        status="active",
        created_at=now - timedelta(hours=2)
    )
    upgrade_db.add(item)
    await upgrade_db.commit()

    success, msg, order = await InventoryService.purchase_item_with_wallet(
        db=upgrade_db,
        item_id=item.id,
        expected_day=1,
        telegram_user={"id": user_id, "username": "tx_buyer", "first_name": "Tx"}
    )
    assert success is True

    # Check ledger transactions
    txs = await WalletService.get_recent_transactions(upgrade_db, user_id)
    tx_types = [t.tx_type for t in txs]
    assert "DEPOSIT" in tx_types
    assert "PURCHASE" in tx_types


# ==========================================
# 9. Screenshot Single Review & Back Button Only
# ==========================================
@pytest.mark.asyncio
async def test_screenshot_single_review_enforcement(upgrade_db: AsyncSession):
    """
    When screenshot is uploaded once, status becomes PENDING_REVIEW.
    If uploaded again while in review, it MUST NOT go to review again.
    """
    from indo_selling_bot.bot.keyboards import get_deposit_request_keyboard
    user_id = 998877
    deposit = await WalletService.create_deposit_request(upgrade_db, user_id, 50)
    await upgrade_db.commit()

    # 1. First upload -> succeeds
    ok1, msg1, dep1 = await WalletService.attach_screenshot(upgrade_db, deposit.reference, "photo_1")
    assert ok1 is True
    assert dep1.status == "PENDING_REVIEW"

    # 2. Second upload -> rejected from review
    ok2, msg2, dep2 = await WalletService.attach_screenshot(upgrade_db, deposit.reference, "photo_2")
    assert ok2 is False
    assert "already been submitted" in msg2.lower()
    assert dep2.status == "PENDING_REVIEW"

    # 3. QR keyboard must contain ONLY the Back button with danger style
    kb = get_deposit_request_keyboard(deposit.reference)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert "Back" in buttons[0].text
    assert buttons[0].callback_data == "cancel_payment"
    assert buttons[0].style == "danger"


# ==========================================
# 10. Buy Indo Only Days Old, Styles, and Batch Purchase
# ==========================================
@pytest.mark.asyncio
async def test_buy_indo_quantity_and_batch_purchase(upgrade_db: AsyncSession):
    """
    Buy Indo keyboard shows only Days Old (no count on button).
    Quantity keyboard provides 1 igs, 5 igs, 10 igs, 20 igs, custom amount.
    Batch purchase atomically deducts wallet and reserves stock.
    """
    from indo_selling_bot.bot.keyboards import get_buy_quantity_keyboard
    user_id = 445566
    now = datetime.now(timezone.utc)

    # Deposit funds: ₹500
    dep = await WalletService.create_deposit_request(upgrade_db, user_id, 500)
    await WalletService.approve_deposit(upgrade_db, dep.reference, 5344918305)

    # Add 25 units in Day 1 Old
    item = InventoryItem(
        sku="TEST-SKU-BATCH",
        category_id=1,
        title="Day 1 Batch Item",
        price=50,
        quantity=25,
        status="active",
        created_at=now - timedelta(hours=2)
    )
    upgrade_db.add(item)
    await upgrade_db.commit()

    # 1. Test quantity keyboard buttons and styles
    qkb = get_buy_quantity_keyboard(1)
    q_texts = [btn.text for row in qkb.inline_keyboard for btn in row]
    assert "1 igs" in q_texts
    assert "5 igs" in q_texts
    assert "10 igs" in q_texts
    assert "20 igs" in q_texts
    assert "✏️ Custom Amount" in q_texts
    assert "« Back to Categories" in q_texts

    # Check button styles
    for row in qkb.inline_keyboard:
        for btn in row:
            if "Back" in btn.text:
                assert btn.style == "danger"
            else:
                assert btn.style == "primary"

    # 2. Buy 5 igs
    user_dict = {"id": user_id, "username": "batch_buyer", "first_name": "Batch"}
    success, msg, order = await InventoryService.purchase_batch_with_wallet(
        db=upgrade_db,
        day_number=1,
        quantity=5,
        telegram_user=user_dict,
        reference_time=now
    )
    assert success is True
    assert order.total_amount == 250.0  # 5 * 50

    # Wallet balance after purchase: 500 - 250 = 250
    bal = await WalletService.get_balance(upgrade_db, user_id)
    assert bal == 250.0

    # Stock remaining: 25 - 5 = 20
    await upgrade_db.refresh(item)
    assert item.quantity == 20


# ==========================================
# 11. Total Stock ASCII Table Format & 0-Stock Hiding
# ==========================================
def test_total_stock_ascii_table_format_and_zero_hiding():
    """
    Total stock formats as ASCII table matching user specification.
    Days with 0 stock MUST NOT be displayed in table.
    """
    from indo_selling_bot.bot.handlers.total_stock import _format_stock_summary
    summary = {
        "total_stock": 17,
        "categories": [
            {"day": 1, "quantity": 0, "dates": []},
            {"day": 2, "quantity": 0, "dates": []},
            {"day": 10, "quantity": 17, "dates": ["2026-10-10"]}
        ]
    }
    table = _format_stock_summary(summary)

    assert "┌──────────────┬─────────┬───────┐" in table
    assert "│ Period       │ Date    │ Stock │" in table
    assert "│ 10 days old  │ 10 Oct  │    17 │" in table
    assert "│ Total                  │    17 │" in table
    assert "└────────────────────────┴───────┘" in table

    # 0-stock days are hidden
    assert "Today" not in table
    assert "1 day old" not in table
