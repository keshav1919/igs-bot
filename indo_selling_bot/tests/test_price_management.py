"""
Comprehensive test suite for Telegram Bot Price Management.

Tests:
1. Category price initialization and updates (Day 1 Old to Day 20 Old).
2. Validation rules (negative price rejection, zero-price authorization check).
3. Live price summary view across all 20 categories.
4. Server-side checkout calculation using CategoryPrice and wallet balance check.
5. Historical receipts preservation (future price changes do not modify past orders).
6. Non-admin authorization rejection on price management handlers.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from aiogram.types import Message, User, Chat, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.config import settings
from indo_selling_bot.database import Base
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.models.order import Order, OrderItem
from indo_selling_bot.models.pricing import CategoryPrice, PriceChangeLog
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.pricing_service import PricingService
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.bot.handlers.admin_pricing import (
    handle_pricing_menu,
    handle_view_all_prices,
    handle_select_price_category,
    handle_category_selected,
    handle_price_input,
    handle_confirm_price,
    AdminPricingState,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def pricing_db():
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


@pytest.mark.asyncio
async def test_set_and_change_category_price(pricing_db: AsyncSession):
    """Admin can set and update category price with audit logs and change history."""
    admin_id = settings.TELEGRAM_ADMIN_USER_ID or 987654321

    # 1. Set price for Day 5 Old to 35,000 IDR
    success, msg, cp = await PricingService.set_category_price(
        db=pricing_db,
        day_number=5,
        new_price=35000.0,
        admin_id=admin_id,
        reason="Initial seasonal pricing"
    )
    assert success is True
    assert cp is not None
    assert cp.price == 35000.0
    assert cp.day_number == 5

    # Check change log
    logs = await PricingService.get_price_history(pricing_db, day_number=5)
    assert len(logs) == 1
    assert logs[0].new_price == 35000.0
    assert logs[0].admin_id == admin_id

    # Check audit log
    audit_stmt = select(AuditLog).where(AuditLog.action == "PRICE_UPDATED")
    audits = (await pricing_db.execute(audit_stmt)).scalars().all()
    assert len(audits) == 1
    assert "35,000" in audits[0].details

    # 2. Later update Day 5 Old price to 42,000 IDR
    success2, msg2, cp2 = await PricingService.set_category_price(
        db=pricing_db,
        day_number=5,
        new_price=42000.0,
        admin_id=admin_id,
        reason="Market demand increase"
    )
    assert success2 is True
    assert cp2.price == 42000.0

    # Verify history now has 2 entries and preserves old_price
    logs_updated = await PricingService.get_price_history(pricing_db, day_number=5)
    assert len(logs_updated) == 2
    assert logs_updated[0].old_price == 35000.0
    assert logs_updated[0].new_price == 42000.0


@pytest.mark.asyncio
async def test_reject_negative_and_invalid_prices(pricing_db: AsyncSession):
    """Price management must reject negative prices and zero without explicit free flag."""
    admin_id = 987654321

    # Negative price rejection
    s1, m1, _ = await PricingService.set_category_price(
        db=pricing_db, day_number=3, new_price=-5000, admin_id=admin_id
    )
    assert s1 is False
    assert "cannot be negative" in m1.lower()

    # Zero price rejection when allow_free is False
    s2, m2, _ = await PricingService.set_category_price(
        db=pricing_db, day_number=3, new_price=0, admin_id=admin_id, allow_free=False
    )
    assert s2 is False
    assert "cannot be zero" in m2.lower()

    # Zero price allowed when allow_free is True
    s3, m3, cp3 = await PricingService.set_category_price(
        db=pricing_db, day_number=3, new_price=0, admin_id=admin_id, allow_free=True
    )
    assert s3 is True
    assert cp3.price == 0.0

    # Invalid day range
    s4, m4, _ = await PricingService.set_category_price(
        db=pricing_db, day_number=25, new_price=10000, admin_id=admin_id
    )
    assert s4 is False
    assert "between 1 and 20" in m4


@pytest.mark.asyncio
async def test_view_all_prices_summary(pricing_db: AsyncSession):
    """View all prices returns all 20 categories with defaults and custom prices."""
    admin_id = 987654321

    # Customize Day 2 and Day 10
    await PricingService.set_category_price(pricing_db, day_number=2, new_price=30000, admin_id=admin_id)
    await PricingService.set_category_price(pricing_db, day_number=10, new_price=15000, admin_id=admin_id)

    all_prices = await PricingService.get_all_category_prices(pricing_db, max_days=20)
    assert len(all_prices) == 20

    day2 = next(p for p in all_prices if p["day"] == 2)
    assert day2["price"] == 30000.0
    assert day2["is_custom"] is True

    day10 = next(p for p in all_prices if p["day"] == 10)
    assert day10["price"] == 15000.0
    assert day10["is_custom"] is True

    day1 = next(p for p in all_prices if p["day"] == 1)
    assert day1["is_custom"] is False


@pytest.mark.asyncio
async def test_checkout_uses_category_price_and_verifies_balance(pricing_db: AsyncSession):
    """
    During checkout:
    1. Total is calculated server-side from CategoryPrice.
    2. Customer with insufficient balance is rejected.
    3. Customer with sufficient balance is charged CategoryPrice, not inventory item price.
    4. Inventory item's own base price is left unmodified.
    """
    admin_id = 987654321
    user_id = 888777
    now = datetime.now(timezone.utc)

    # 1. Setup Day 3 inventory item with internal batch price 10,000 IDR
    item = InventoryItem(
        sku="TEST-SKU-PRICING",
        category_id=1,
        title="Day 3 Indo Item",
        price=10000.0,  # Old / item-level price
        quantity=3,
        created_at=now - timedelta(days=2, hours=3),  # Exactly Day 3
        status="active"
    )
    pricing_db.add(item)
    await pricing_db.commit()

    # 2. Admin sets category price for Day 3 to 35,000 IDR
    await PricingService.set_category_price(
        db=pricing_db,
        day_number=3,
        new_price=35000.0,
        admin_id=admin_id
    )

    # 3. User wallet has 20,000 IDR (sufficient for item price 10k, but INSUFFICIENT for category price 35k)
    wallet = await WalletService.get_or_create_wallet(pricing_db, user_id)
    wallet.balance = 20000
    await pricing_db.commit()

    # 4. Attempt purchase -> Must be rejected due to category price (35,000 IDR)
    success, msg, order = await InventoryService.purchase_item_with_wallet(
        db=pricing_db,
        item_id=item.id,
        expected_day=3,
        telegram_user={"id": user_id, "username": "buyer", "first_name": "Buyer"},
        reference_time=now
    )
    assert success is False
    assert "Insufficient balance" in msg
    assert order is None

    # Wallet and stock remain untouched
    bal_after_fail = await WalletService.get_balance(pricing_db, user_id)
    assert bal_after_fail == 20000.0
    await pricing_db.refresh(item)
    assert item.quantity == 3

    # 5. User adds funds: balance now 60,000 IDR
    wallet.balance = 60000
    await pricing_db.commit()

    # 6. Purchase now succeeds
    success2, msg2, order2 = await InventoryService.purchase_item_with_wallet(
        db=pricing_db,
        item_id=item.id,
        expected_day=3,
        telegram_user={"id": user_id, "username": "buyer", "first_name": "Buyer"},
        reference_time=now
    )
    assert success2 is True
    assert order2 is not None
    # Charged category price of 35,000 IDR
    assert order2.total_amount == 35000.0

    # Check wallet was debited by 35,000 (60,000 - 35,000 = 25,000)
    bal_after_success = await WalletService.get_balance(pricing_db, user_id)
    assert bal_after_success == 25000.0

    # Underlying inventory item base price was NOT altered
    await pricing_db.refresh(item)
    assert item.price == 10000.0
    assert item.quantity == 2


@pytest.mark.asyncio
async def test_historical_receipts_preserved_after_price_change(pricing_db: AsyncSession):
    """Changing category prices in the future must NEVER alter past orders or receipts."""
    admin_id = 987654321
    user_id = 666555
    now = datetime.now(timezone.utc)

    # 1. Setup item in Day 1 Old
    item = InventoryItem(
        sku="TEST-SKU-HIST",
        category_id=1,
        title="Day 1 Indo Item",
        price=20000.0,
        quantity=5,
        created_at=now - timedelta(hours=2),
        status="active"
    )
    pricing_db.add(item)

    # 2. Admin sets Day 1 price to 25,000 IDR
    await PricingService.set_category_price(pricing_db, day_number=1, new_price=25000.0, admin_id=admin_id)

    # 3. Customer buys at 25,000 IDR
    wallet = await WalletService.get_or_create_wallet(pricing_db, user_id)
    wallet.balance = 50000
    await pricing_db.commit()

    ok, _, order = await InventoryService.purchase_item_with_wallet(
        db=pricing_db,
        item_id=item.id,
        expected_day=1,
        telegram_user={"id": user_id, "username": "hist_buyer", "first_name": "Hist"},
        reference_time=now
    )
    assert ok is True
    assert order.total_amount == 25000.0
    order_id = order.id

    # 4. Later, admin changes Day 1 price to 75,000 IDR
    await PricingService.set_category_price(pricing_db, day_number=1, new_price=75000.0, admin_id=admin_id)

    # 5. Verify the past order record and its items STILL have the original 25,000 IDR receipt amount
    past_order_stmt = select(Order).where(Order.id == order_id)
    past_order = (await pricing_db.execute(past_order_stmt)).scalar_one()
    assert past_order.total_amount == 25000.0

    item_stmt = select(OrderItem).where(OrderItem.order_id == order_id)
    order_item = (await pricing_db.execute(item_stmt)).scalar_one()
    assert order_item.unit_price == 25000.0
    assert order_item.subtotal == 25000.0


@pytest.mark.asyncio
async def test_non_admin_cannot_access_price_management(pricing_db: AsyncSession):
    """Non-admin user IDs are strictly rejected from all price management operations."""
    unauthorized_user = User(id=111222, is_bot=False, first_name="Intruder", username="intruder")
    chat = Chat(id=111222, type="private")

    mock_msg = AsyncMock(spec=Message)
    mock_msg.from_user = unauthorized_user
    mock_msg.chat = chat
    mock_msg.text = "💵 Price Management"
    mock_msg.answer = AsyncMock()

    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=123, chat_id=111222, user_id=111222))

    # Attempt to open pricing menu
    await handle_pricing_menu(mock_msg, state)
    mock_msg.answer.assert_called_once()
    assert "Unauthorized" in mock_msg.answer.call_args[0][0]

    # Attempt to view all prices
    mock_msg.answer.reset_mock()
    await handle_view_all_prices(mock_msg, state)
    assert "Unauthorized" in mock_msg.answer.call_args[0][0]

    # Attempt callback query
    mock_cb = AsyncMock(spec=CallbackQuery)
    mock_cb.from_user = unauthorized_user
    mock_cb.data = "admin:price:set"
    mock_cb.answer = AsyncMock()
    await handle_select_price_category(mock_cb, state)
    mock_cb.answer.assert_called_once()
    assert "Unauthorized" in mock_cb.answer.call_args[0][0]
