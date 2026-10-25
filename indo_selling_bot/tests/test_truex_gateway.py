"""
Comprehensive Unit & Integration Test Suite for TrueXGateway Payment Integration.
Tests:
- Order reference generation matching TG_PAY_MVNVLVM5_768
- Unique decimal amount calculation for auto-detection
- Success caption text template formatting
- Atomic wallet crediting and idempotency
- Real-time status checking and polling manager
"""
import pytest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.models.wallet import Customer, Wallet, DepositRequest, WalletTransaction
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.truex_service import (
    TrueXGatewayService,
    TrueXPollingManager,
    format_success_caption,
)
from indo_selling_bot.config import settings


@pytest.mark.asyncio
async def test_order_ref_format(seeded_db: AsyncSession):
    """Verify order reference strictly follows TG_PAY_{8 chars}_{3 digits} pattern."""
    ref = await TrueXGatewayService.generate_order_ref(seeded_db)
    assert ref.startswith("TG_PAY_")
    parts = ref.split("_")
    assert len(parts) == 4  # ['TG', 'PAY', 'XXXXXXXX', 'XXX']
    assert len(parts[2]) == 8
    assert len(parts[3]) == 3
    assert parts[3].isdigit()


@pytest.mark.asyncio
async def test_calculate_unique_amount(seeded_db: AsyncSession):
    """Verify unique decimal amount generation."""
    # Round base amount e.g. 50 -> returns 50.xx with decimals
    unique_amt = await TrueXGatewayService.calculate_unique_amount(seeded_db, 50.0)
    assert 50.01 <= unique_amt <= 50.99
    assert unique_amt != 50.0

    # User entered 40 -> text shows without decimal (40), but actual unique QR amount has decimal (e.g. 40.xx)
    base_amt = 40.0
    display_amt = int(base_amt) if float(base_amt).is_integer() else f"{base_amt:.2f}"
    actual_qr_amt = await TrueXGatewayService.calculate_unique_amount(seeded_db, base_amt)
    assert display_amt == 40
    assert 40.01 <= actual_qr_amt <= 40.99
    assert actual_qr_amt != 40.0

    # User explicit decimal e.g. 1.02 -> keeps 1.02
    explicit_amt = await TrueXGatewayService.calculate_unique_amount(seeded_db, 1.02)
    assert explicit_amt == 1.02


def test_format_success_caption():
    """Verify success caption matches the exact required format."""
    dt = datetime(2026, 10, 25, 19, 11, tzinfo=ZoneInfo("Asia/Kolkata"))
    caption = format_success_caption(1.02, "TG_PAY_MVNVLVM5_768", dt)

    assert " 𝐏𝐀𝐘𝐌𝐄𝐍𝐓 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋 ✅ " in caption
    assert "———————————————-" in caption
    assert "💰 Amount:  ₹1.02" in caption
    assert "🆔 Order ID: TG_PAY_MVNVLVM5_768" in caption
    assert "🕒 Paid At: 25 Oct, 07:11 PM" in caption


@pytest.mark.asyncio
async def test_deposit_creation_and_atomic_credit(seeded_db: AsyncSession):
    """
    Verify creating deposit request, approving it, atomic balance update,
    ledger entry, and idempotency (repeated approval must not double-credit).
    Also verifies admin user can deposit small amounts (e.g. ₹1.02) without ₹30 limit.
    """
    admin_id = settings.TELEGRAM_ADMIN_USER_ID or 5232576810
    await WalletService.get_or_create_customer(seeded_db, admin_id, "test_txg_admin")
    initial_bal = await WalletService.get_balance(seeded_db, admin_id)
    assert initial_bal == 0.0

    order_ref = await TrueXGatewayService.generate_order_ref(seeded_db)
    deposit = await WalletService.create_deposit_request(
        db=seeded_db,
        telegram_user_id=admin_id,
        amount=1.02,
        reference=order_ref,
        txg_order_id="TXGTEST12345",
        initial_status="PENDING_PAYMENT"
    )
    await seeded_db.commit()

    assert deposit.reference == order_ref
    assert deposit.amount == 1.02
    assert deposit.status == "PENDING_PAYMENT"
    assert deposit.txg_order_id == "TXGTEST12345"

    # Mock Bot class
    class MockBot:
        def __init__(self):
            self.edited_messages = []
            self.stickers_sent = []
            self.admin_messages = []

        async def edit_message_media(self, chat_id, message_id, media, reply_markup=None):
            self.edited_messages.append((chat_id, message_id, media))

        async def send_sticker(self, chat_id, sticker):
            self.stickers_sent.append((chat_id, sticker))

        async def send_message(self, chat_id, text, parse_mode=None):
            self.admin_messages.append((chat_id, text))

    bot = MockBot()

    # Process successful payment
    success = await TrueXGatewayService.process_successful_payment(
        bot=bot,
        order_ref=order_ref,
        utr="UTR_998811223344",
        txg_order_id="TXGTEST12345",
        db=seeded_db
    )
    assert success is True

    # Verify wallet balance credited
    new_bal = await WalletService.get_balance(seeded_db, admin_id)
    assert new_bal == 1.02

    # Verify ledger entry
    stmt_tx = select(WalletTransaction).where(WalletTransaction.reference == order_ref)
    tx = (await seeded_db.execute(stmt_tx)).scalar_one_or_none()
    assert tx is not None
    assert tx.tx_type == "DEPOSIT"
    assert tx.amount == 1.02
    assert tx.balance_after == 1.02

    # Verify IDEMPOTENCY: Repeated call must return False and NOT credit again
    success_duplicate = await TrueXGatewayService.process_successful_payment(
        bot=bot,
        order_ref=order_ref,
        utr="UTR_998811223344",
        txg_order_id="TXGTEST12345",
        db=seeded_db
    )
    assert success_duplicate is False
    bal_after_duplicate = await WalletService.get_balance(seeded_db, admin_id)
    assert bal_after_duplicate == 1.02  # Still 1.02, never 2.04!


@pytest.mark.asyncio
async def test_polling_manager_start_and_stop():
    """Verify polling manager can register and cancel polling tasks cleanly."""
    mgr = TrueXPollingManager()

    class DummyBot:
        pass

    bot = DummyBot()
    await mgr.start_polling(bot, "TXG_TEST_999", "TG_PAY_REF_999", timeout_seconds=2)
    assert "TXG_TEST_999" in mgr._active_tasks

    await mgr.stop_polling("TXG_TEST_999")
    assert "TXG_TEST_999" not in mgr._active_tasks
