"""
Phase 2 Comprehensive Verification Tests.
Tests timestamp-driven day grouping, boundary conditions, deactivation/activation,
quick restock with change logging, bot token validation & masking, and custom error pages.
"""
from datetime import datetime, timezone, timedelta
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import Settings, settings
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.security.auth import create_session_token
from indo_selling_bot.security.csrf import generate_csrf_token


# =========================================================================
# 1. TIMESTAMP-BASED DAY GROUPING & BOUNDARY TESTS
# =========================================================================

def test_day_group_exact_boundaries():
    """Verify exact hour boundaries for Day 1 through Day 10+."""
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 0 hours old -> Day 1 (age_days = 0)
    t0 = now
    assert InventoryService.calculate_day_group_from_timestamp(t0, reference_time=now) == "Day 1"

    # 23 hours 59 minutes old -> Day 1 (age_days = 0)
    t_23h = now - timedelta(hours=23, minutes=59)
    assert InventoryService.calculate_day_group_from_timestamp(t_23h, reference_time=now) == "Day 1"

    # Exactly 24 hours old -> Day 2 (age_days = 1)
    t_24h = now - timedelta(hours=24)
    assert InventoryService.calculate_day_group_from_timestamp(t_24h, reference_time=now) == "Day 2"

    # 48 hours old -> Day 3 (age_days = 2)
    t_48h = now - timedelta(hours=48)
    assert InventoryService.calculate_day_group_from_timestamp(t_48h, reference_time=now) == "Day 3"

    # 216 hours old (9 full days) -> Day 10 (age_days = 9)
    t_216h = now - timedelta(hours=216)
    assert InventoryService.calculate_day_group_from_timestamp(t_216h, reference_time=now) == "Day 10"

    # 239 hours old -> Day 10 (age_days = 9)
    t_239h = now - timedelta(hours=239)
    assert InventoryService.calculate_day_group_from_timestamp(t_239h, reference_time=now) == "Day 10"

    # 240 hours old (10 full days) -> Day 10+ (age_days = 10)
    t_240h = now - timedelta(hours=240)
    assert InventoryService.calculate_day_group_from_timestamp(t_240h, reference_time=now) == "Day 10+"

    # 30 days old -> Day 10+ (age_days = 30)
    t_30d = now - timedelta(days=30)
    assert InventoryService.calculate_day_group_from_timestamp(t_30d, reference_time=now) == "Day 10+"


def test_day_group_future_timestamps_and_naive_datetimes():
    """Verify resilience against future timestamps or naive datetimes."""
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    # Future timestamp (clock drift) should safely default to Day 1
    future_time = now + timedelta(hours=5)
    assert InventoryService.calculate_day_group_from_timestamp(future_time, reference_time=now) == "Day 1"

    # Naive timestamp should be treated as UTC without crashing
    naive_created = datetime(2026, 10, 18, 12, 0, 0)
    day_group = InventoryService.calculate_day_group_from_timestamp(naive_created, reference_time=now)
    assert day_group == "Day 2"

    # None created_at defaults to Day 1
    assert InventoryService.calculate_day_group_from_timestamp(None, reference_time=now) == "Day 1"


def test_inventory_item_model_age_methods():
    """Verify InventoryItem helper methods get_age_days and get_day_group."""
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    item = InventoryItem(
        title="Test Item",
        sku="TEST-SKU-001",
        price=50000,
        quantity=10,
        created_at=now - timedelta(days=3, hours=2)
    )

    assert item.get_age_days(reference_time=now) == 3
    assert item.get_day_group(reference_time=now) == "Day 4"


# =========================================================================
# 2. INVENTORY ACTIVATION, DEACTIVATION & QUICK RESTOCK
# =========================================================================

@pytest.mark.asyncio
async def test_inventory_deactivation_and_activation(seeded_db: AsyncSession):
    """Test pausing/deactivating an item and re-activating it with audit logging."""
    item = await InventoryService.create_item(
        db=seeded_db,
        sku="P2-ACT-001",
        category_id=1,
        title="Active Item 1",
        price=15000,
        quantity=8,
        admin_user="superadmin"
    )
    await seeded_db.commit()
    assert item.status == "active"

    # Deactivate / Pause
    deactivated = await InventoryService.deactivate_item(
        seeded_db, item.id, admin_user="superadmin"
    )
    await seeded_db.commit()
    assert deactivated.status == "paused"

    # Check audit log for deactivation
    log_stmt = select(InventoryChangeLog).where(
        InventoryChangeLog.item_sku == item.sku,
        InventoryChangeLog.action == "DEACTIVATE"
    )
    logs = list((await seeded_db.execute(log_stmt)).scalars().all())
    assert len(logs) == 1
    assert logs[0].admin_user == "superadmin"

    # Reactivate
    reactivated = await InventoryService.activate_item(
        seeded_db, item.id, admin_user="superadmin"
    )
    await seeded_db.commit()
    assert reactivated.status == "active"

    # Check audit log for reactivation
    log_stmt2 = select(InventoryChangeLog).where(
        InventoryChangeLog.item_sku == item.sku,
        InventoryChangeLog.action == "ACTIVATE"
    )
    logs2 = list((await seeded_db.execute(log_stmt2)).scalars().all())
    assert len(logs2) == 1


@pytest.mark.asyncio
async def test_quick_restock_and_auto_activation(seeded_db: AsyncSession):
    """Test quick restock of an out-of-stock item auto-activating."""
    item = await InventoryService.create_item(
        db=seeded_db,
        sku="P2-DEP-001",
        category_id=1,
        title="Depleted Item",
        price=20000,
        quantity=0,
        status="out_of_stock",
        admin_user="superadmin"
    )
    await seeded_db.commit()
    assert item.quantity == 0
    assert item.status == "out_of_stock"

    # Quick restock +15
    restocked = await InventoryService.quick_restock(
        seeded_db,
        item_id=item.id,
        additional_quantity=15,
        admin_user="superadmin"
    )
    await seeded_db.commit()
    assert restocked.quantity == 15
    assert restocked.status == "active"

    # Verify audit log
    log_stmt = select(InventoryChangeLog).where(
        InventoryChangeLog.item_sku == item.sku,
        InventoryChangeLog.action == "UPDATE_STOCK"
    )
    logs = list((await seeded_db.execute(log_stmt)).scalars().all())
    assert len(logs) >= 1
    restock_log = logs[-1]
    assert restock_log.old_value == "0"
    assert restock_log.new_value == "15"


# =========================================================================
# 3. BOT TOKEN VALIDATION & MASKING
# =========================================================================

def test_bot_token_validation_and_masking():
    """Verify Telegram Bot token validation logic and secure masking."""
    # Empty / None
    is_valid, msg = Settings.validate_bot_token(None)
    assert not is_valid
    assert "empty" in msg.lower()

    is_valid, msg = Settings.validate_bot_token("")
    assert not is_valid

    # Placeholder tokens
    is_valid, msg = Settings.validate_bot_token("YOUR_TELEGRAM_BOT_TOKEN")
    assert not is_valid
    assert "placeholder" in msg.lower()

    # Missing colon separator
    is_valid, msg = Settings.validate_bot_token("123456789abcdefghijklmnopqrstuvwxyz")
    assert not is_valid
    assert "colon" in msg.lower()

    # Invalid bot ID prefix
    is_valid, msg = Settings.validate_bot_token("notanumber:abcdefghijklmnopqrstuvwxyz")
    assert not is_valid
    assert "numeric" in msg.lower()

    # Short secret
    is_valid, msg = Settings.validate_bot_token("123456789:abc")
    assert not is_valid
    assert "too short" in msg.lower()

    # Valid token structure
    valid_token = "123456789:AAFlkJ98sd98f7s9d8f7sd98f7sdf"
    is_valid, msg = Settings.validate_bot_token(valid_token)
    assert is_valid
    assert "valid" in msg.lower()

    # Secure masking (never leak full token in logs)
    masked = Settings.mask_token(valid_token)
    assert masked.startswith("123456")
    assert masked.endswith("sdf")
    assert "****" in masked
    assert valid_token not in masked

    assert Settings.mask_token("") == "[NOT CONFIGURED]"


# =========================================================================
# 4. CUSTOM ERROR PAGES & WEB ROUTES
# =========================================================================

@pytest.mark.asyncio
async def test_custom_404_error_page_html(async_client: AsyncClient):
    """Verify custom 404 HTML template renders when requesting a non-existent page."""
    resp = await async_client.get("/non-existent-page-phase2", headers={"accept": "text/html"})
    assert resp.status_code == 404
    assert "text/html" in resp.headers.get("content-type", "")
    assert "404" in resp.text
    assert "Page or Listing Not Found" in resp.text
    assert "Return to Dashboard" in resp.text


@pytest.mark.asyncio
async def test_custom_404_api_json(async_client: AsyncClient):
    """Verify 404 returns JSON for API endpoints or json accept header."""
    resp = await async_client.get("/api/non-existent-endpoint", headers={"accept": "application/json"})
    assert resp.status_code == 404
    assert "application/json" in resp.headers.get("content-type", "")
    data = resp.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_web_quick_restock_and_toggle_status_endpoints(async_client: AsyncClient, seeded_db: AsyncSession):
    """Verify web router endpoints for toggle-status and quick-restock."""
    session_token = create_session_token(1, "testadmin", "superadmin")
    csrf_token = generate_csrf_token(session_token)

    # 1. Fetch existing item from seeded_db
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    assert item is not None
    assert item.status == "active"
    item_id = item.id
    original_qty = item.quantity

    # Set auth cookies directly on client to avoid per-request cookie deprecation
    async_client.cookies.set(settings.SESSION_COOKIE_NAME, session_token)

    # 2. POST to /{item_id}/toggle-status
    resp_toggle = await async_client.post(
        f"/inventory/{item_id}/toggle-status",
        data={"csrf_token": csrf_token},
        follow_redirects=False
    )
    assert resp_toggle.status_code == 303
    assert resp_toggle.headers["location"].startswith("/inventory")

    # Fetch latest state using populate_existing=True to refresh in-memory instance
    stmt1 = select(InventoryItem).execution_options(populate_existing=True).where(InventoryItem.id == item_id)
    refreshed = (await seeded_db.execute(stmt1)).scalar_one()
    assert refreshed.status == "paused"

    # 3. POST to /{item_id}/quick-restock
    resp_restock = await async_client.post(
        f"/inventory/{item_id}/quick-restock",
        data={"csrf_token": csrf_token, "additional_quantity": "25"},
        follow_redirects=False
    )
    assert resp_restock.status_code == 303
    assert resp_restock.headers["location"].startswith("/inventory")

    # Fetch latest stock using populate_existing=True
    stmt2 = select(InventoryItem).execution_options(populate_existing=True).where(InventoryItem.id == item_id)
    refreshed2 = (await seeded_db.execute(stmt2)).scalar_one()
    assert refreshed2.quantity == original_qty + 25
