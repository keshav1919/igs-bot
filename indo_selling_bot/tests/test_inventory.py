"""
Unit tests for Inventory management, day-based groupings, stock alerts, and audit change history.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.models.inventory import InventoryItem

@pytest.mark.asyncio
async def test_create_inventory_item_with_day_group(seeded_db: AsyncSession):
    item = await InventoryService.create_item(
        db=seeded_db,
        sku="TEST-CREATE-D3",
        category_id=1,
        title="Software Pro Day 3",
        price=120000.0,
        quantity=25,
        day_group="Day 3",
        currency="IDR",
        stock_alert_threshold=5,
        admin_user="unit_tester"
    )
    await seeded_db.commit()

    assert item.id is not None
    assert item.sku == "TEST-CREATE-D3"
    assert item.day_group == "Day 3"
    assert item.quantity == 25
    assert item.is_available is True

    # Verify audit change log entry was recorded
    from sqlalchemy import select
    from indo_selling_bot.models.inventory import InventoryChangeLog
    log_stmt = select(InventoryChangeLog).where(InventoryChangeLog.item_sku == item.sku)
    logs = list((await seeded_db.execute(log_stmt)).scalars().all())
    assert len(logs) >= 1
    assert logs[0].action == "CREATE"
    assert logs[0].admin_user == "unit_tester"

@pytest.mark.asyncio
async def test_update_item_change_logging(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    assert item is not None

    # Update price and quantity
    updates = {"price": 55000.0, "quantity": 18, "day_group": "Day 2"}
    updated = await InventoryService.update_item(seeded_db, item, updates, admin_user="admin_editor")
    await seeded_db.commit()

    assert updated.price == 55000.0
    assert updated.quantity == 18
    assert updated.day_group == "Day 2"

    # Verify audit change logs were recorded for each field
    from sqlalchemy import select
    from indo_selling_bot.models.inventory import InventoryChangeLog
    log_stmt = select(InventoryChangeLog).where(InventoryChangeLog.item_sku == item.sku)
    logs = list((await seeded_db.execute(log_stmt)).scalars().all())
    actions = [log.action for log in logs]
    assert "UPDATE_STOCK" in actions
    assert "UPDATE_DETAILS" in actions

@pytest.mark.asyncio
async def test_list_items_filtering_and_day_groups(seeded_db: AsyncSession):
    # Filter by day group "Day 5"
    items, total, pages = await InventoryService.list_items(
        db=seeded_db,
        day_group="Day 5"
    )
    assert total == 1
    assert items[0].sku == "TEST-LIC-D5"

    # Filter by search keyword
    items_search, total_search, _ = await InventoryService.list_items(
        db=seeded_db,
        search="License"
    )
    assert total_search >= 2

@pytest.mark.asyncio
async def test_stock_alerts(seeded_db: AsyncSession):
    # Fetch low stock items (TEST-LIC-D5 has qty 2, threshold 5)
    alerts = await InventoryService.get_low_stock_alerts(seeded_db)
    assert len(alerts) >= 1
    skus = [a.sku for a in alerts]
    assert "TEST-LIC-D5" in skus

@pytest.mark.asyncio
async def test_stock_deduction_and_insufficient_protection(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    initial_qty = item.quantity

    # Deduct valid quantity
    deducted = await InventoryService.deduct_stock(seeded_db, item.id, 3)
    await seeded_db.commit()
    assert deducted is True

    refetched = await InventoryService.get_by_id(seeded_db, item.id)
    assert refetched.quantity == initial_qty - 3

    # Attempt to deduct more than remaining stock
    failed_deduct = await InventoryService.deduct_stock(seeded_db, item.id, 9999)
    assert failed_deduct is False

@pytest.mark.asyncio
async def test_restock_item(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    old_qty = item.quantity

    restocked = await InventoryService.restock_item(seeded_db, item.id, 5)
    await seeded_db.commit()
    assert restocked is True

    refetched = await InventoryService.get_by_id(seeded_db, item.id)
    assert refetched.quantity == old_qty + 5

@pytest.mark.asyncio
async def test_delete_item_with_audit(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D5")
    item_id = item.id

    deleted = await InventoryService.delete_item(seeded_db, item_id, admin_user="admin_deleter")
    await seeded_db.commit()
    assert deleted is True

    assert await InventoryService.get_by_id(seeded_db, item_id) is None
