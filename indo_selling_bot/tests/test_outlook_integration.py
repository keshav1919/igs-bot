"""
Unit tests for Outlook Bot Integration, Quick Access 4-dot persistent keyboard,
and wallet-based purchasing.
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat, CallbackQuery, ReplyKeyboardMarkup, InlineKeyboardMarkup

from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db, AsyncSessionLocal
from indo_selling_bot.models.outlook_account import OutlookAccount
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.services.outlook_service import OutlookService
from indo_selling_bot.services.mail_service import (
    parse_credential_line,
    parse_all_credentials,
    extract_code_from_text
)
from indo_selling_bot.bot.keyboards import (
    get_main_reply_keyboard,
    get_start_welcome_keyboard,
    get_outlook_accounts_keyboard
)
from indo_selling_bot.bot.handlers.start import handle_start
from indo_selling_bot.bot.handlers.outlook import (
    handle_outlook_entry,
    handle_buy_confirm,
    handle_buy_execute,
    handle_del_do
)

@pytest.fixture(autouse=True)
async def prepare_test_db():
    await init_db()

@pytest.mark.asyncio
async def test_quick_access_reply_keyboard_has_persistent_4_dots():
    """Verify quick access keyboard has is_persistent=False (to allow toggle open/close) and Outlook beside Buy Indo."""
    kb = get_main_reply_keyboard(is_admin=False)
    assert isinstance(kb, ReplyKeyboardMarkup)
    assert kb.is_persistent is False
    assert kb.resize_keyboard is True

    # Row 1 must have Buy Indo and Outlook side-by-side
    row1_texts = [btn.text for btn in kb.keyboard[0]]
    assert "🛒 Buy Indo" in row1_texts
    assert "📩 Outlook" in row1_texts

@pytest.mark.asyncio
async def test_start_welcome_inline_keyboard_has_outlook():
    """Verify /start inline keyboard has Outlook button."""
    kb = get_start_welcome_keyboard()
    assert isinstance(kb, InlineKeyboardMarkup)
    all_btns = [btn.text for row in kb.inline_keyboard for btn in row]
    assert any("Buy Indo" in t for t in all_btns)
    assert any("Outlook" in t for t in all_btns)
    assert any("Free Indo ig" in t for t in all_btns)

@pytest.mark.asyncio
async def test_mail_credential_parser():
    """Test line parsing for Outlook credentials."""
    line = "testuser99@outlook.com|MyPass#123|M.C519_SN1.TOKEN|9e5f94bc-e8a4-4e73-b8be-63364c29d753"
    res = parse_credential_line(line)
    assert res is not None
    assert res["email"] == "testuser99@outlook.com"
    assert res["password"] == "MyPass#123"
    assert res["refresh_token"] == "M.C519_SN1.TOKEN"
    assert res["client_id"] == "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

@pytest.mark.asyncio
async def test_otp_code_extraction():
    """Test OTP code extractor."""
    subj = "123456 is your Instagram code"
    body = "Hi, someone requested to sign up with this code: 123456"
    assert extract_code_from_text(subj, body) == "123456"

@pytest.mark.asyncio
async def test_outlook_service_buy_and_restock():
    """Test buying an Outlook account using wallet balance and restocking."""
    user_id = 8881234

    # 1. Add balance to user
    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, user_id, "test_user", "Test")
        wallet = await WalletService.get_or_create_wallet(db, user_id)
        wallet.balance = 10
        await db.commit()

    # 2. Add an account to stock
    unique_email = f"auto_test_{uuid.uuid4().hex[:8]}@outlook.com"
    creds = [{
        "email": unique_email,
        "password": "Password123!",
        "refresh_token": "TOKEN_ABC",
        "client_id": "11111111-2222-3333-4444-555555555555"
    }]
    added, _ = await OutlookService.add_stock_bulk(creds, price=3.0)
    assert added >= 1

    # 3. Buy account
    ok, reason, acc = await OutlookService.buy_outlook_account(user_id=user_id, price=3.0)
    assert ok is True
    assert acc["is_sold"] is True
    assert acc["user_id"] == user_id
    assert "@" in acc["email"]

    # Check balance after purchase
    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, user_id)
        assert bal == 7.0

    # 4. Restock account
    restocked = await OutlookService.restock_account(account_id=acc["id"], user_id=user_id)
    assert restocked is True

    # Account should now be unsold
    async with AsyncSessionLocal() as db:
        acc_db = await OutlookService.get_account_by_id(account_id=acc["id"], user_id=user_id)
        assert acc_db is None  # no longer owned by user
