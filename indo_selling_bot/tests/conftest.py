"""
Pytest fixtures and test database configuration.
Uses in-memory SQLite with foreign keys enabled for test speed and complete isolation.
"""
import sys
from pathlib import Path

# Bootstrap sys.path to enable 'indo_selling_bot' package imports when run directly from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT.parent))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if "indo_selling_bot" not in sys.modules:
    try:
        import indo_selling_bot
    except ModuleNotFoundError:
        import types
        pkg = types.ModuleType("indo_selling_bot")
        pkg.__path__ = [str(_PROJECT_ROOT)]
        pkg.__file__ = str(_PROJECT_ROOT / "__init__.py")
        sys.modules["indo_selling_bot"] = pkg

import pytest
import asyncio
from typing import AsyncGenerator
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from indo_selling_bot.database import Base, get_db
from indo_selling_bot.models.user import User
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.order import Order, OrderItem, OrderStatus
from indo_selling_bot.models.setting import StoreSetting
from indo_selling_bot.models.wallet import Customer, Wallet, WalletTransaction, DepositRequest
from indo_selling_bot.models.import_history import StockImportHistory
from indo_selling_bot.security.hashing import hash_password
from indo_selling_bot.security.auth import create_session_token
from indo_selling_bot.web.app import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def test_engine():
    """Create a fresh in-memory SQLite async engine."""
    engine = create_async_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        echo=False
    )
    async with engine.begin() as conn:
        await conn.exec_driver_sql("PRAGMA foreign_keys = ON;")
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an isolated database session rolled back or fresh per test."""
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session

@pytest.fixture
async def seeded_db(db_session: AsyncSession) -> AsyncSession:
    """Pre-populate test database with admin, category, day-grouped items, and settings."""
    # 1. Admin
    admin = User(
        username="testadmin",
        password_hash=hash_password("SecretPass@2026"),
        email="testadmin@example.com",
        role="superadmin",
        is_active=True
    )
    db_session.add(admin)

    # 2. Categories
    cat1 = Category(name="Software Licenses", slug="software-licenses", icon="🔑", is_active=True, sort_order=1)
    cat2 = Category(name="Cloud Credits", slug="cloud-credits", icon="☁️", is_active=True, sort_order=2)
    db_session.add_all([cat1, cat2])
    await db_session.flush()

    # 3. Items with Day-based Grouping
    item1 = InventoryItem(
        sku="TEST-LIC-D1",
        category_id=cat1.id,
        title="Test License Key Day 1",
        day_group="Day 1",
        price=50000.0,
        currency="IDR",
        quantity=10,
        stock_alert_threshold=5,
        status="active",
        fulfillment_payload="KEY-ABCD-1122-3344",
        internal_notes="Test stock Day 1"
    )
    item2 = InventoryItem(
        sku="TEST-LIC-D5",
        category_id=cat1.id,
        title="Test License Key Day 5",
        day_group="Day 5",
        price=75000.0,
        currency="IDR",
        quantity=2,  # Low stock
        stock_alert_threshold=5,
        status="active",
        fulfillment_payload="KEY-DAY5-XYZ-9999",
        internal_notes="Low stock item"
    )
    db_session.add_all([item1, item2])

    # 4. Settings
    setting_dg = StoreSetting(
        key="day_groups",
        value="Day 1, Day 2, Day 3, Day 4, Day 5, Day 6, Day 7, Day 8, Day 9, Day 10"
    )
    db_session.add(setting_dg)

    await db_session.commit()
    return db_session

@pytest.fixture
async def async_client(test_engine, seeded_db) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client configured to use the test database session."""
    session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()

@pytest.fixture
def admin_session_cookie() -> str:
    """Generate a valid signed admin session cookie string."""
    return create_session_token(user_id=1, username="testadmin", role="superadmin")
