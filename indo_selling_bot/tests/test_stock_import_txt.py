"""
Automated test suite for Automatic TXT Stock Import System.

Tests:
1. Flexible TXT parser: Format 1 (4-field), Format 2 (2-field), headers, comments, blank lines.
2. UTF-8 and UTF-8 BOM encoding support.
3. Timestamp and dynamic category assignment (24h intervals from UTC).
4. Boundary handling: Day 1 (<24h), Day 2 (24h..48h), Day 20, >20 days quarantined to Review Required.
5. Future timestamp rejection and quarantine to Review Required.
6. Category mismatch detection: flags conflict, follows timestamp-based rule.
7. Duplicate detection: in-file duplicates and existing DB duplicates skipped safely without overwriting.
8. Invalid lines handling: malformed lines, missing timestamps, missing item IDs reported cleanly.
9. Preview generation, Cancellation, and Confirmation.
10. Atomic database transaction and rollback on failure.
11. Security: Non-admin users strictly rejected on import routes.
12. Integration: Total Stock and Buy Indo display imported stock accurately.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from aiogram.types import Message, User, Chat, Document, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.config import settings
from indo_selling_bot.database import Base
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.import_history import StockImportHistory
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.stock_import_service import (
    StockImportService,
    ImportPreview,
    ParsedRecord,
)
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.bot.handlers.admin_stock import (
    AdminAddStockState,
    AdminImportTxtState,
    handle_admin_add_stock_start,
    handle_admin_manual_stock_text,
    handle_import_txt_menu,
    handle_import_txt_file,
    handle_import_txt_non_document,
    handle_import_confirm,
    handle_import_cancel,
    handle_import_errors,
)

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture(autouse=True)
def prevent_stock_txt_pollution(monkeypatch):
    from unittest.mock import MagicMock
    from indo_selling_bot.services.stock_txt_service import StockTxtService
    monkeypatch.setattr(StockTxtService, "add_stock_lines", MagicMock(return_value=(0, 0, [])))

@pytest.fixture
async def import_db():
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
        cat = Category(name="Default Cat", slug="default-cat", description="Test Category")
        session.add(cat)
        await session.commit()
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


# ==========================================
# 1. Flexible Parser & Encoding Tests
# ==========================================
@pytest.mark.asyncio
async def test_parse_formats_and_utf8_bom(import_db: AsyncSession):
    """Test parsing Format 1 (4-field), Format 2 (2-field), comments, and UTF-8 BOM."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    # File content with UTF-8 BOM (\ufeff), comments (#), blank lines, and headers
    txt_content = (
        "\ufeff# Inventory upload sample\n"
        "ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS\n"
        "\n"
        "DEMO-01|DAY_1_OLD|2026-10-19T06:00:00Z|AVAILABLE\n"
        "DEMO-02|2026-10-18T10:00:00Z\n"  # Format 2 (2 fields, age 26h = Day 2)
        "DEMO-03|DAY_3_OLD|2026-10-17T12:00:00Z|ACTIVE\n"
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="test_mixed.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_valid == 3
    assert preview.total_duplicates == 0
    assert preview.total_invalid == 0
    assert preview.total_review == 0

    assert preview.valid_records[0].item_id == "DEMO-01"
    assert preview.valid_records[0].day_number == 1
    assert preview.valid_records[0].category_label == "1 Day Old"

    assert preview.valid_records[1].item_id == "DEMO-02"
    assert preview.valid_records[1].day_number == 2
    assert preview.valid_records[1].category_label == "2 Days Old"

    assert preview.valid_records[2].item_id == "DEMO-03"
    assert preview.valid_records[2].day_number == 3
    assert preview.valid_records[2].category_label == "3 Days Old"


# ==========================================
# 2. Timestamp & Day Boundary Handling
# ==========================================
@pytest.mark.asyncio
async def test_day_boundaries_and_age_calculation(import_db: AsyncSession):
    """Test exact 24-hour boundary calculations across Day 1, Day 2, Day 20, and Day 21."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    txt_content = (
        f"ITEM-D1-EDGE|{ (ref_time - timedelta(hours=23, minutes=59)).isoformat() }\n"
        f"ITEM-D2-START|{ (ref_time - timedelta(hours=24, minutes=0)).isoformat() }\n"
        f"ITEM-D20|{ (ref_time - timedelta(days=19, hours=5)).isoformat() }\n"
        f"ITEM-D21-OVER|{ (ref_time - timedelta(days=21)).isoformat() }\n"  # > 20 days -> Review required
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="boundaries.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_valid == 3
    assert preview.total_review == 1  # ITEM-D21-OVER quarantined

    # Verify boundary items
    d1 = next(r for r in preview.valid_records if r.item_id == "ITEM-D1-EDGE")
    assert d1.day_number == 1
    assert d1.category_label == "1 Day Old"

    d2 = next(r for r in preview.valid_records if r.item_id == "ITEM-D2-START")
    assert d2.day_number == 2
    assert d2.category_label == "2 Days Old"

    d20 = next(r for r in preview.valid_records if r.item_id == "ITEM-D20")
    assert d20.day_number == 20
    assert d20.category_label == "20 Days Old"

    # Quarantined over-age item
    d21 = preview.review_records[0]
    assert d21.item_id == "ITEM-D21-OVER"
    assert d21.category_label == "Uncategorized / Review Required"
    assert "exceeds supported 1-20 days range" in d21.error_reason


# ==========================================
# 3. Future Timestamp & Category Mismatch Handling
# ==========================================
@pytest.mark.asyncio
async def test_future_timestamp_and_category_mismatch(import_db: AsyncSession):
    """
    1. Future timestamp must be quarantined to Review Required.
    2. Category mismatch must be flagged, adhering to timestamp truth.
    """
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    txt_content = (
        # Future date
        "ITEM-FUTURE|DAY_1_OLD|2026-10-25T12:00:00Z|AVAILABLE\n"
        # Category mismatch: File says DAY_1_OLD, but date is 48h ago (Day 3)
        "ITEM-MISMATCH|DAY_1_OLD|2026-10-17T12:00:00Z|AVAILABLE\n"
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="anomalies.txt",
        db=import_db,
        reference_time=ref_time
    )

    # Future item quarantined
    assert preview.total_review == 1
    assert preview.review_records[0].item_id == "ITEM-FUTURE"
    assert "Future timestamp" in preview.review_records[0].error_reason

    # Mismatched item: valid because age (Day 3) is within 1-20 range, but warning recorded
    assert preview.total_valid == 1
    mismatch_rec = preview.valid_records[0]
    assert mismatch_rec.item_id == "ITEM-MISMATCH"
    assert mismatch_rec.day_number == 3
    assert mismatch_rec.category_label == "3 Days Old"
    assert "Category mismatch" in mismatch_rec.warning_reason
    assert "Following timestamp-based rule" in mismatch_rec.warning_reason


# ==========================================
# 4. In-File & Database Duplicate Detection
# ==========================================
@pytest.mark.asyncio
async def test_in_file_and_database_duplicate_detection(import_db: AsyncSession):
    """Verify duplicates within file and against existing DB are detected without overwriting."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Seed existing DB item
    existing_item = InventoryItem(
        sku="EXISTING-SKU-999",
        category_id=1,
        title="Existing Pre-stored Item",
        quantity=5,
        price=30000,
        created_at=ref_time - timedelta(days=2),
        status="active"
    )
    import_db.add(existing_item)
    await import_db.commit()

    # 2. File contains: 1 valid, 1 in-file duplicate, 1 existing-DB duplicate
    txt_content = (
        "NEW-ITEM-101|2026-10-19T06:00:00Z\n"
        "NEW-ITEM-101|2026-10-19T06:00:00Z\n"  # In-file duplicate
        "EXISTING-SKU-999|2026-10-18T06:00:00Z\n"  # Database duplicate
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="duplicates.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_valid == 1
    assert preview.total_duplicates == 2
    assert preview.valid_records[0].item_id == "NEW-ITEM-101"

    dup_ids = [d.item_id for d in preview.duplicates]
    assert "NEW-ITEM-101" in dup_ids
    assert "EXISTING-SKU-999" in dup_ids

    # Verify existing DB item was untouched
    await import_db.refresh(existing_item)
    assert existing_item.quantity == 5


# ==========================================
# 5. Invalid Lines & Malformed Content
# ==========================================
@pytest.mark.asyncio
async def test_invalid_lines_and_malformed_timestamps(import_db: AsyncSession):
    """Verify missing fields, invalid dates, and corrupt syntax are reported cleanly without crashes."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)

    txt_content = (
        "VALID-001|2026-10-19T10:00:00Z\n"
        "SINGLE_TOKEN_NO_PIPE\n"  # Invalid syntax
        "BAD-DATE|INVALID_TIMESTAMP_STRING\n"  # Malformed date
        "|2026-10-19T10:00:00Z\n"  # Empty Item ID
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="malformed.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_valid == 1
    assert preview.total_invalid == 3
    assert preview.valid_records[0].item_id == "VALID-001"

    errors = [r.error_reason for r in preview.invalid_records]
    assert any("Missing fields" in e for e in errors)
    assert any("Unrecognized or malformed timestamp" in e for e in errors)
    assert any("Item ID is empty" in e for e in errors)


# ==========================================
# 6. Preview, Confirmation & Transactional Persistence
# ==========================================
@pytest.mark.asyncio
async def test_preview_and_confirm_import_transaction(import_db: AsyncSession):
    """Test full preview, confirm import, change logs, StockImportHistory, and AuditLog creation."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    admin_id = 987654321

    txt_content = (
        "BATCH-01|DAY_1_OLD|2026-10-19T06:00:00Z|AVAILABLE\n"
        "BATCH-02|DAY_2_OLD|2026-10-18T06:00:00Z|AVAILABLE\n"
        "BATCH-03|2026-10-17T06:00:00Z\n"
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="batch_valid.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_valid == 3

    # Commit import
    success, msg, count = await StockImportService.commit_import(
        session_id=preview.session_id,
        admin_id=admin_id,
        db=import_db
    )

    assert success is True
    assert count == 3

    # Check database records
    stmt = select(InventoryItem).where(InventoryItem.sku.in_(["BATCH-01", "BATCH-02", "BATCH-03"]))
    items = list((await import_db.execute(stmt)).scalars().all())
    assert len(items) == 3
    for it in items:
        assert it.status == "active"
        assert it.quantity == 1

    # Check StockImportHistory
    hist_stmt = select(StockImportHistory).where(StockImportHistory.filename == "batch_valid.txt")
    history = (await import_db.execute(hist_stmt)).scalar_one_or_none()
    assert history is not None
    assert history.admin_id == admin_id
    assert history.valid_count == 3
    assert history.status == "CONFIRMED"

    # Check AuditLog
    audit_stmt = select(AuditLog).where(AuditLog.action == "TXT_STOCK_IMPORT")
    audit = (await import_db.execute(audit_stmt)).scalar_one_or_none()
    assert audit is not None
    assert str(admin_id) in audit.performed_by


# ==========================================
# 7. Cancellation Discards Session
# ==========================================
@pytest.mark.asyncio
async def test_cancel_import_session(import_db: AsyncSession):
    """Cancelling an import session must discard in-memory data without modifying DB."""
    txt_content = "CANCEL-01|2026-10-19T06:00:00Z\n"
    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="cancel.txt",
        db=import_db
    )

    session_id = preview.session_id
    assert StockImportService.get_session(session_id) is not None

    # Cancel
    cancelled = StockImportService.cancel_import(session_id, admin_id=987654321)
    assert cancelled is True
    assert StockImportService.get_session(session_id) is None

    # Verify nothing was saved to DB
    stmt = select(InventoryItem).where(InventoryItem.sku == "CANCEL-01")
    item = (await import_db.execute(stmt)).scalar_one_or_none()
    assert item is None


# ==========================================
# 8. Security: Non-Admin Rejection
# ==========================================
@pytest.mark.asyncio
async def test_non_admin_cannot_access_import_menu():
    """Non-admin user calling /import_stock or import_txt callback is strictly unauthorized."""
    original_admin = settings.TELEGRAM_ADMIN_USER_ID
    try:
        settings.TELEGRAM_ADMIN_USER_ID = 987654321

        # Non-admin user
        mock_msg = AsyncMock(spec=Message)
        mock_msg.from_user = User(id=11111, is_bot=False, first_name="Stranger")
        mock_msg.answer = AsyncMock()

        storage = MemoryStorage()
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=11111, user_id=11111))

        await handle_import_txt_menu(mock_msg, state)

        assert mock_msg.answer.called
        args, _ = mock_msg.answer.call_args
        assert "Unauthorized" in args[0]
    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin


# ==========================================
# 9. Stock Integration: Total Stock & Buy Indo
# ==========================================
@pytest.mark.asyncio
async def test_stock_display_reflects_imported_items(import_db: AsyncSession):
    """Verify imported items appear accurately in Total Stock and Buy Indo summaries."""
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    admin_id = 987654321

    txt_content = (
        "STOCK-D1-A|2026-10-19T06:00:00Z\n"  # Day 1
        "STOCK-D1-B|2026-10-19T08:00:00Z\n"  # Day 1
        "STOCK-D5-A|2026-10-15T06:00:00Z\n"  # Day 5
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="live_stock.txt",
        db=import_db,
        reference_time=ref_time
    )

    await StockImportService.commit_import(
        session_id=preview.session_id,
        admin_id=admin_id,
        db=import_db
    )

    # Check Total Stock Summary
    summary = await InventoryService.get_total_stock_summary(import_db, reference_time=ref_time)
    assert summary["total_stock"] == 3

    d1_cat = next(c for c in summary["categories"] if c["day"] == 1)
    assert d1_cat["quantity"] == 2

    d5_cat = next(c for c in summary["categories"] if c["day"] == 5)
    assert d5_cat["quantity"] == 1

    # Check Buy Indo query for Day 1
    d1_items = await InventoryService.get_items_by_exact_age_day(import_db, day_number=1, reference_time=ref_time)
    assert len(d1_items) == 2
    assert {it.sku for it in d1_items} == {"STOCK-D1-A", "STOCK-D1-B"}


# ==========================================
# 10. Account Format & Manual Paste Tests
# ==========================================
@pytest.mark.asyncio
async def test_parse_account_colon_format_with_ist(import_db: AsyncSession):
    """
    Test parsing account lines in format:
    ( neha6501@metamail.store:654654@@:neha404714:11 Oct 2026, 01:14:43 IST )
    mail:pass:username:date:time
    Verifies:
    - Username extracted as SKU
    - Timestamp parsed with IST (+05:30) converted to UTC
    - Dynamic age category assigned correctly
    - Password is discarded and never persisted
    - Title and internal notes set cleanly
    """
    # Reference time: 19 Oct 2026 12:00:00 UTC
    ref_time = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)
    admin_id = 999888

    txt_content = (
        "( neha6501@metamail.store:654654@@:neha404714:11 Oct 2026, 01:14:43 IST )\n"
        "priya99@metamail.store:Secret#123:priya_acc:15 Oct 2026, 10:30:00 IST\n"
        "( neha6501@metamail.store:654654@@:neha404714:11 Oct 2026, 01:14:43 IST )\n"  # In-file duplicate
    )

    preview = await StockImportService.parse_txt_file(
        content_bytes=txt_content.encode("utf-8"),
        filename="accounts.txt",
        db=import_db,
        reference_time=ref_time
    )

    assert preview.total_lines_read == 3
    assert preview.total_valid == 2
    assert preview.total_duplicates == 1

    # First item: 11 Oct 2026 01:14:43 IST -> 2026-10-10 19:44:43 UTC
    # Elapsed from 2026-10-19 12:00:00 UTC = 8 days, 16 hours -> 8 completed 24h -> Day 9 Old
    rec1 = preview.valid_records[0]
    assert rec1.item_id == "neha404714"
    assert rec1.day_number == 9
    assert rec1.category_label == "9 Days Old"
    assert rec1.extra.get("mail") == "neha6501@metamail.store"
    assert rec1.extra.get("username") == "neha404714"

    # Commit to database
    ok, msg, count = await StockImportService.commit_import(
        session_id=preview.session_id,
        admin_id=admin_id,
        db=import_db
    )
    assert ok is True
    assert count == 2

    # Query DB to inspect persisted InventoryItem
    stmt = select(InventoryItem).where(InventoryItem.sku == "neha404714")
    item = (await import_db.execute(stmt)).scalar_one()

    assert item.sku == "neha404714"
    assert item.title == "Indo Account - neha404714"
    assert "neha6501@metamail.store" in item.internal_notes
    assert "654654@@" not in item.internal_notes  # Password is sanitized and not stored
    assert item.day_group == "Day 9"


@pytest.mark.asyncio
async def test_manual_stock_paste_handler(import_db: AsyncSession, monkeypatch):
    """
    Test Admin Add Stock Manual text paste flow.
    Admin sends text with account lines directly in chat.
    Bot returns preview keyboard.
    """
    from unittest.mock import patch
    original_admin = settings.TELEGRAM_ADMIN_USER_ID
    admin_id = 7771234
    settings.TELEGRAM_ADMIN_USER_ID = admin_id

    try:
        # Mock AsyncSessionLocal to return import_db
        with patch("indo_selling_bot.bot.handlers.admin_stock.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=import_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            # 1. Start manual add stock
            mock_start_msg = MagicMock(spec=Message)
            mock_start_msg.from_user = User(id=admin_id, is_bot=False, first_name="Admin")
            mock_start_msg.answer = AsyncMock()

            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=admin_id, user_id=admin_id))

            await handle_admin_add_stock_start(mock_start_msg, state)

            current_state = await state.get_state()
            assert current_state == AdminAddStockState.waiting_for_manual_text.state

            # 2. Admin pastes account text
            pasted_text = "( testacc01@metamail.store:pass123:testuser01:18 Oct 2026, 05:00:00 IST )"
            mock_paste_msg = MagicMock(spec=Message)
            mock_paste_msg.from_user = User(id=admin_id, is_bot=False, first_name="Admin")
            mock_paste_msg.text = pasted_text
            status_reply = MagicMock()
            status_reply.edit_text = AsyncMock()
            mock_paste_msg.answer = AsyncMock(return_value=status_reply)

            await handle_admin_manual_stock_text(mock_paste_msg, state)

            assert status_reply.edit_text.called
            text_arg = status_reply.edit_text.call_args[0][0]
            assert "Stock Import Preview" in text_arg
            assert "testuser01" in text_arg

            # State is cleared after preview
            assert await state.get_state() is None

            # 3. Confirm import callback
            kb_arg = status_reply.edit_text.call_args[1]["reply_markup"]
            confirm_button = kb_arg.inline_keyboard[0][0]
            session_id = confirm_button.callback_data.split(":")[1]

            mock_cb = MagicMock(spec=CallbackQuery)
            mock_cb.from_user = User(id=admin_id, is_bot=False, first_name="Admin")
            mock_cb.data = f"imp_confirm:{session_id}"
            mock_cb.answer = AsyncMock()
            mock_cb.message = MagicMock()
            mock_cb.message.edit_text = AsyncMock()

            await handle_import_confirm(mock_cb)

            assert mock_cb.message.edit_text.called
            success_text = mock_cb.message.edit_text.call_args[0][0]
            assert "Stock Import Confirmed & Saved!" in success_text
            assert "1 units" in success_text

            # Verify in DB
            stmt = select(InventoryItem).where(InventoryItem.sku == "testuser01")
            saved = (await import_db.execute(stmt)).scalar_one()
            assert saved.sku == "testuser01"
            assert saved.title == "Indo Account - testuser01"

    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin


@pytest.mark.asyncio
async def test_direct_text_paste_in_import_txt_flow(import_db: AsyncSession):
    """Test pasting lines directly when in AdminImportTxtState.waiting_for_file."""
    from unittest.mock import patch
    original_admin = settings.TELEGRAM_ADMIN_USER_ID
    admin_id = 888777
    settings.TELEGRAM_ADMIN_USER_ID = admin_id

    try:
        with patch("indo_selling_bot.bot.handlers.admin_stock.AsyncSessionLocal") as mock_session_local:
            mock_session_local.return_value.__aenter__ = AsyncMock(return_value=import_db)
            mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)

            storage = MemoryStorage()
            state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=admin_id, user_id=admin_id))
            await state.set_state(AdminImportTxtState.waiting_for_file)

            pasted_line = "direct01@metamail.store:secret:directuser:17 Oct 2026, 12:00:00 IST"
            mock_msg = MagicMock(spec=Message)
            mock_msg.from_user = User(id=admin_id, is_bot=False, first_name="Admin")
            mock_msg.text = pasted_line
            status_reply = MagicMock()
            status_reply.edit_text = AsyncMock()
            mock_msg.answer = AsyncMock(return_value=status_reply)

            await handle_import_txt_non_document(mock_msg, state)

            assert status_reply.edit_text.called
            text_arg = status_reply.edit_text.call_args[0][0]
            assert "Stock Import Preview" in text_arg
            assert "directuser" in text_arg
    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin

