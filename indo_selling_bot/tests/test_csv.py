"""
Unit tests for CSV import, export, and template generation.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.services.csv_service import CSVService
from indo_selling_bot.services.inventory_service import InventoryService

@pytest.mark.asyncio
async def test_inventory_csv_export(seeded_db: AsyncSession):
    csv_str = await CSVService.export_inventory_csv(seeded_db)
    assert "sku,title,category,day_group,price" in csv_str
    assert "TEST-LIC-D1" in csv_str
    assert "TEST-LIC-D5" in csv_str

def test_inventory_sample_template():
    template = CSVService.get_inventory_sample_template()
    assert "sku,title,category,day_group,price,quantity" in template
    assert "LEGIT-KEY-D1" in template

@pytest.mark.asyncio
async def test_inventory_csv_import(seeded_db: AsyncSession):
    csv_data = """sku,title,category,day_group,price,quantity,currency,stock_alert_threshold,status,fulfillment_type,fulfillment_payload,internal_notes,description
CSV-NEW-D4,Imported Cloud Key,Cloud Services,Day 4,99000,10,IDR,5,active,digital_asset,PAYLOAD-999,Internal batch note,Public description
TEST-LIC-D1,Updated Title For D1,Software Licenses,Day 1,60000,30,IDR,5,active,digital_asset,KEY-UPDATED,Updated note,Description
"""
    result = await CSVService.import_inventory_csv(seeded_db, csv_data, admin_user="csv_tester")
    await seeded_db.commit()

    assert result["success"] is True
    assert result["created"] == 1
    assert result["updated"] == 1
    assert len(result["errors"]) == 0

    # Verify new item created
    new_item = await InventoryService.get_by_sku(seeded_db, "CSV-NEW-D4")
    assert new_item is not None
    assert new_item.price == 99000.0
    assert new_item.day_group == "Day 4"

    # Verify updated item
    updated_item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    assert updated_item.price == 60000.0
    assert updated_item.quantity == 30

@pytest.mark.asyncio
async def test_inventory_csv_import_missing_headers(seeded_db: AsyncSession):
    bad_csv = "random_col,other_col\nval1,val2"
    result = await CSVService.import_inventory_csv(seeded_db, bad_csv)
    assert result["success"] is False
    assert "Missing required CSV columns" in result["error"]

@pytest.mark.asyncio
async def test_orders_and_audit_csv_exports(seeded_db: AsyncSession):
    orders_csv = await CSVService.export_orders_csv(seeded_db)
    assert "order_code,telegram_user_id,telegram_username" in orders_csv

    audit_csv = await CSVService.export_audit_logs_csv(seeded_db)
    assert "id,action,performed_by,ip_address" in audit_csv
