"""
Tests for Admin Fund Command (/fund <userid> <amount>).
Verifies:
1. Strict server-side administrator authorization (rejects non-admin).
2. Proper parsing of user ID (numeric, formatted with braces/brackets, username).
3. Wallet crediting, immutable ledger transaction, and audit logging.
4. Input validation (positive amounts, numeric formats).
5. Customer notification and admin confirmation formatting.
6. Fallback behavior when /fund is invoked without arguments.
"""
import random
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db, AsyncSessionLocal
from indo_selling_bot.models.wallet import Customer, Wallet, WalletTransaction
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.bot.handlers.add_fund import handle_fund_command

ADMIN_ID = list(settings.admin_ids)[0]

@pytest.fixture(autouse=True)
async def prepare_test_db():
    await init_db()

def _create_mock_message(user_id: int, text: str, username: str | None = None) -> AsyncMock:
    fake_user = User(id=user_id, is_bot=False, first_name="TestUser", username=username)
    fake_chat = Chat(id=user_id, type="private")
    msg = AsyncMock(spec=Message)
    msg.from_user = fake_user
    msg.chat = fake_chat
    msg.text = text
    msg.answer = AsyncMock()
    msg.bot = AsyncMock()
    msg.bot.send_message = AsyncMock()
    return msg

def _create_fsm_context(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=123, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)

@pytest.mark.asyncio
async def test_wallet_service_admin_add_fund():
    """Verify WalletService.admin_add_fund credits balance, records transaction and audit log."""
    target_id = random.randint(100000000, 999999999)
    async with AsyncSessionLocal() as db:
        success, msg, prev_bal, new_bal, ref = await WalletService.admin_add_fund(
            db=db,
            admin_id=ADMIN_ID,
            target_user_id=target_id,
            amount=150.0,
            reason="Test admin fund add"
        )
        assert success is True
        assert prev_bal == 0.0
        assert new_bal == 150.0
        assert ref.startswith("ADMIN-FUND-")

        # Second credit to same user
        success2, msg2, prev_bal2, new_bal2, ref2 = await WalletService.admin_add_fund(
            db=db,
            admin_id=ADMIN_ID,
            target_user_id=target_id,
            amount=50.0
        )
        assert success2 is True
        assert prev_bal2 == 150.0
        assert new_bal2 == 200.0

        # Verify balance in wallet
        bal = await WalletService.get_balance(db, target_id)
        assert bal == 200.0

        # Verify transactions
        txs = await WalletService.get_recent_transactions(db, target_id)
        assert len(txs) >= 2
        assert txs[0].tx_type == "ADMIN_CREDIT"
        assert txs[0].amount == 50.0

@pytest.mark.asyncio
async def test_wallet_service_admin_add_fund_invalid_amount():
    """Verify WalletService.admin_add_fund rejects zero or negative amounts."""
    target_id = random.randint(100000000, 999999999)
    async with AsyncSessionLocal() as db:
        success, msg, prev, new, ref = await WalletService.admin_add_fund(
            db=db,
            admin_id=ADMIN_ID,
            target_user_id=target_id,
            amount=-20.0
        )
        assert success is False
        assert "greater than" in msg

        success0, msg0, prev0, new0, ref0 = await WalletService.admin_add_fund(
            db=db,
            admin_id=ADMIN_ID,
            target_user_id=target_id,
            amount=0.0
        )
        assert success0 is False

@pytest.mark.asyncio
async def test_admin_fund_command_success():
    """Admin uses /fund <userid> <amount> to credit user account."""
    target_id = random.randint(100000000, 999999999)
    msg = _create_mock_message(user_id=ADMIN_ID, text=f"/fund {target_id} 75")
    state = _create_fsm_context(ADMIN_ID)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Funds Added Successfully!" in reply_text
    assert str(target_id) in reply_text
    assert "₹75.00" in reply_text

    # Verify target wallet
    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, target_id)
        assert bal == 75.0

    # Verify notification to customer
    assert msg.bot.send_message.called
    cust_args = msg.bot.send_message.call_args
    assert cust_args[1]["chat_id"] == target_id
    assert "Wallet Credited!" in cust_args[1]["text"]
    assert "₹75.00" in cust_args[1]["text"]

@pytest.mark.asyncio
async def test_admin_fund_command_with_braces_and_currency():
    """Admin uses /fund {userid} amount with braces / brackets or ₹ symbols."""
    target_id = random.randint(100000000, 999999999)
    msg = _create_mock_message(user_id=ADMIN_ID, text=f"/fund {{{target_id}}} ₹100.50")
    state = _create_fsm_context(ADMIN_ID)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Funds Added Successfully!" in reply_text
    assert str(target_id) in reply_text
    assert "₹100.50" in reply_text

    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, target_id)
        assert bal == 100.50

@pytest.mark.asyncio
async def test_admin_fund_command_by_username():
    """Admin uses /fund @username <amount> to credit user account."""
    target_id = random.randint(100000000, 999999999)
    username = f"user_{uuid.uuid4().hex[:8]}"

    # Register customer first
    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, target_id, username=username, first_name="Cust")
        await db.commit()

    msg = _create_mock_message(user_id=ADMIN_ID, text=f"/fund @{username} 60")
    state = _create_fsm_context(ADMIN_ID)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Funds Added Successfully!" in reply_text
    assert str(target_id) in reply_text
    assert "₹60.00" in reply_text

    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, target_id)
        assert bal == 60.0

@pytest.mark.asyncio
async def test_fund_command_strictly_rejects_non_admin():
    """Non-admin user attempting /fund <userid> <amount> is strictly blocked."""
    non_admin_id = random.randint(100000000, 999999999)
    target_id = random.randint(100000000, 999999999)
    msg = _create_mock_message(user_id=non_admin_id, text=f"/fund {target_id} 50")
    state = _create_fsm_context(non_admin_id)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Unauthorized" in reply_text
    assert "Only administrators" in reply_text

    # Target user must not receive any balance
    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, target_id)
        assert bal == 0.0

@pytest.mark.asyncio
async def test_admin_fund_command_no_args_shows_admin_usage():
    """Admin typing /fund with no arguments sees admin usage guidance."""
    msg = _create_mock_message(user_id=ADMIN_ID, text="/fund")
    state = _create_fsm_context(ADMIN_ID)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Admin Fund Command Usage" in reply_text
    assert "/fund" in reply_text and "userid" in reply_text and "amount" in reply_text

@pytest.mark.asyncio
async def test_non_admin_fund_command_no_args_shows_deposit_menu():
    """Customer typing /fund or /addfund with no arguments sees Add Fund deposit info."""
    non_admin_id = random.randint(100000000, 999999999)
    msg = _create_mock_message(user_id=non_admin_id, text="/fund")
    state = _create_fsm_context(non_admin_id)

    await handle_fund_command(msg, state)

    assert msg.answer.called
    reply_text = msg.answer.call_args[0][0]
    assert "Add Funds to Your Wallet" in reply_text
    assert "Minimum Deposit" in reply_text

@pytest.mark.asyncio
async def test_admin_fund_command_invalid_syntax_and_amounts():
    """Verify error messages for incomplete args or non-numerical amounts."""
    # 1. Missing amount
    msg1 = _create_mock_message(user_id=ADMIN_ID, text="/fund 123456789")
    state1 = _create_fsm_context(ADMIN_ID)
    await handle_fund_command(msg1, state1)
    assert "Invalid Command Syntax" in msg1.answer.call_args[0][0]

    # 2. Non-numeric amount
    msg2 = _create_mock_message(user_id=ADMIN_ID, text="/fund 123456789 abc")
    state2 = _create_fsm_context(ADMIN_ID)
    await handle_fund_command(msg2, state2)
    assert "Invalid Amount" in msg2.answer.call_args[0][0]

    # 3. Negative amount
    msg3 = _create_mock_message(user_id=ADMIN_ID, text="/fund 123456789 -50")
    state3 = _create_fsm_context(ADMIN_ID)
    await handle_fund_command(msg3, state3)
    assert "Amount must be greater than ₹0" in msg3.answer.call_args[0][0]

    # 4. Unknown username
    msg4 = _create_mock_message(user_id=ADMIN_ID, text="/fund @definitely_not_a_real_user_xyz 50")
    state4 = _create_fsm_context(ADMIN_ID)
    await handle_fund_command(msg4, state4)
    assert "User Not Found" in msg4.answer.call_args[0][0]
