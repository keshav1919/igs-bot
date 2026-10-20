"""
Unit test suite for Channel Join Verification and 1 Free Indo IG Claim.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, User, Chat, CallbackQuery, InlineKeyboardMarkup

from sqlalchemy import delete
from indo_selling_bot.config import settings
from indo_selling_bot.database import Base, AsyncSessionLocal
from indo_selling_bot.models.free_claim import FreeClaim
from indo_selling_bot.services.channel_service import ChannelService
from indo_selling_bot.services.free_claim_service import FreeClaimService
from indo_selling_bot.services.stock_txt_service import StockTxtService
from indo_selling_bot.bot.middlewares import ForceSubscriptionMiddleware
from indo_selling_bot.bot.keyboards import (
    CHANNEL_VERIFICATION_TEXT,
    get_channel_verification_keyboard,
    get_start_welcome_keyboard,
)
from indo_selling_bot.bot.handlers.start import (
    handle_start,
    handle_verify_channel_callback,
    handle_claim_free_ig_callback,
)

@pytest.fixture(autouse=True)
async def isolated_stock_for_claim(monkeypatch, tmp_path):
    """Ensure tests run against a dummy stock file so real stock.txt is never touched."""
    from indo_selling_bot.database import init_db
    await init_db()

    # Clear free_claims table for test isolation
    async with AsyncSessionLocal() as db:
        await db.execute(delete(FreeClaim))
        await db.commit()

    test_stock = tmp_path / "test_stock.txt"
    test_sold = tmp_path / "test_sold.txt"

    # Write test lines
    lines = [
        "free_user1@metamail.store:Pass1@@:user1:15 Oct 2026, 10:00:00 IST\n",
        "free_user2@metamail.store:Pass2@@:user2:15 Oct 2026, 11:00:00 IST\n",
        "free_user3@metamail.store:Pass3@@:user3:15 Oct 2026, 12:00:00 IST\n",
        "free_user4@metamail.store:Pass4@@:user4:15 Oct 2026, 13:00:00 IST\n",
        "free_user5@metamail.store:Pass5@@:user5:15 Oct 2026, 14:00:00 IST\n",
    ]
    test_stock.write_text("".join(lines), encoding="utf-8")
    test_sold.write_text("", encoding="utf-8")

    monkeypatch.setattr(StockTxtService, "get_stock_path", lambda: test_stock)
    monkeypatch.setattr(StockTxtService, "get_sold_path", lambda: test_sold)
    StockTxtService.invalidate_cache()
    yield
    StockTxtService.invalidate_cache()

@pytest.mark.asyncio
async def test_channel_service_subscription_statuses():
    """Verify channel service accurately distinguishes member vs non-member statuses."""
    bot = AsyncMock()

    # Admin bypass
    assert await ChannelService.is_user_subscribed(bot, 5232576810) is True

    # Regular member
    bot.get_chat_member.return_value = MagicMock(status="member")
    assert await ChannelService.is_user_subscribed(bot, 999111) is True

    # Administrator
    bot.get_chat_member.return_value = MagicMock(status="administrator")
    assert await ChannelService.is_user_subscribed(bot, 999111) is True

    # Creator / Owner
    bot.get_chat_member.return_value = MagicMock(status="creator")
    assert await ChannelService.is_user_subscribed(bot, 999111) is True

    # Left channel
    bot.get_chat_member.return_value = MagicMock(status="left")
    assert await ChannelService.is_user_subscribed(bot, 999111) is False

    # Kicked / banned
    bot.get_chat_member.return_value = MagicMock(status="kicked")
    assert await ChannelService.is_user_subscribed(bot, 999111) is False

@pytest.mark.asyncio
async def test_force_subscription_middleware():
    """Verify middleware strictly blocks unverified non-admin users from running bot commands."""
    middleware = ForceSubscriptionMiddleware()
    handler = AsyncMock(return_value="OK")

    bot = AsyncMock()
    bot.get_chat_member.return_value = MagicMock(status="left")

    user = User(id=888999, is_bot=False, first_name="TestUser")
    msg = AsyncMock(spec=Message)
    msg.text = "🛒 Buy Indo"
    msg.answer = AsyncMock()

    data = {"event_from_user": user, "bot": bot}

    # Unsubscribed user message should be intercepted
    res = await middleware(handler, msg, data)
    assert res is None
    assert handler.call_count == 0
    assert msg.answer.called
    assert "Oii Chanel join kr chup Chap" in msg.answer.call_args[0][0]

    # Callback query from unsubscribed user should get alert
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "buy_day:5"
    cb.answer = AsyncMock()
    res_cb = await middleware(handler, cb, data)
    assert res_cb is None
    assert cb.answer.called
    assert "Oii Chanel join kr chup Chap" in cb.answer.call_args[0][0]

    # Admin bypasses middleware
    admin_user = User(id=5232576810, is_bot=False, first_name="Admin")
    admin_data = {"event_from_user": admin_user, "bot": bot}
    res_admin = await middleware(handler, msg, admin_data)
    assert res_admin == "OK"
    assert handler.call_count == 1

@pytest.mark.asyncio
async def test_start_handler_unsubscribed_vs_subscribed():
    """Verify /start displays verification screen for unverified users, and welcome for verified."""
    bot = AsyncMock()
    bot.get_chat_member.return_value = MagicMock(status="left")

    user = User(id=777666, is_bot=False, first_name="Ravi")
    msg = AsyncMock(spec=Message)
    msg.from_user = user
    msg.bot = bot
    msg.answer = AsyncMock()

    # 1. Unsubscribed user
    await handle_start(msg)
    assert msg.answer.called
    text = msg.answer.call_args[0][0]
    assert "Oii Chanel join kr chup Chap" in text
    kb = msg.answer.call_args[1]["reply_markup"]
    assert any("Join Chanel" in btn.text for row in kb.inline_keyboard for btn in row)
    assert any("Verify" in btn.text for row in kb.inline_keyboard for btn in row)
    assert any("Free Indo ig" in btn.text for row in kb.inline_keyboard for btn in row)

    # 2. Subscribed user
    bot.get_chat_member.return_value = MagicMock(status="member")
    msg.answer.reset_mock()
    await handle_start(msg)
    assert msg.answer.called
    welcome_text = msg.answer.call_args[0][0]
    assert "Welcome Legend Indo Bot, Ravi" in welcome_text
    welcome_kb = msg.answer.call_args[1]["reply_markup"]
    assert any("Buy Indo" in btn.text for row in welcome_kb.inline_keyboard for btn in row)
    assert any("Free Indo ig" in btn.text for row in welcome_kb.inline_keyboard for btn in row)

@pytest.mark.asyncio
async def test_verify_callback():
    """Verify clicking verify button tests membership and updates screen."""
    bot = AsyncMock()
    user = User(id=444555, is_bot=False, first_name="Sunil")
    cb = AsyncMock(spec=CallbackQuery)
    cb.from_user = user
    cb.bot = bot
    cb.answer = AsyncMock()
    cb.message = AsyncMock()
    cb.message.edit_text = AsyncMock()

    # When NOT joined
    bot.get_chat_member.return_value = MagicMock(status="left")
    await handle_verify_channel_callback(cb)
    assert cb.answer.called
    assert "Oii Chanel join kr chup Chap" in cb.answer.call_args[0][0]
    assert not cb.message.edit_text.called

    # When JOINED
    bot.get_chat_member.return_value = MagicMock(status="member")
    cb.answer.reset_mock()
    await handle_verify_channel_callback(cb)
    assert cb.answer.called
    assert "Verified" in cb.answer.call_args[0][0]
    assert cb.message.edit_text.called
    assert "Welcome Legend Indo Bot, Sunil" in cb.message.edit_text.call_args[0][0]

@pytest.mark.asyncio
async def test_free_indo_claim_strictly_once():
    """Verify a user can claim exactly 1 free Indo IG, and subsequent claims are rejected."""
    bot = AsyncMock()
    bot.get_chat_member.return_value = MagicMock(status="member")
    bot.send_message = AsyncMock()

    user = User(id=333222, is_bot=False, first_name="Karan", username="karan_dev")
    cb = AsyncMock(spec=CallbackQuery)
    cb.from_user = user
    cb.bot = bot
    cb.answer = AsyncMock()
    cb.message = AsyncMock()
    cb.message.answer = AsyncMock()

    # 1. First claim succeeds
    await handle_claim_free_ig_callback(cb)
    assert cb.answer.called
    assert "claimed" in cb.answer.call_args[0][0].lower()
    assert cb.message.answer.called
    delivery_text = cb.message.answer.call_args[0][0]
    assert "Your Free Indo IG" in delivery_text
    assert "free_user" in delivery_text

    # 2. Second claim must be rejected
    cb.answer.reset_mock()
    cb.message.answer.reset_mock()
    await handle_claim_free_ig_callback(cb)
    assert cb.answer.called
    alert_text = cb.answer.call_args[0][0]
    assert "already claimed" in alert_text.lower() or "alredy clamed" in alert_text.lower()
    assert not cb.message.answer.called

@pytest.mark.asyncio
async def test_free_indo_concurrent_claims_prevent_double_claim():
    """Simulate 5 simultaneous clicks on Free Indo IG button by the same user."""
    test_user_id = 999888777
    username = "race_tester"

    results = await asyncio.gather(*[
        FreeClaimService.claim_free_ig(test_user_id, username)
        for _ in range(5)
    ])

    success_count = sum(1 for ok, msg, acc in results if ok)
    fail_count = sum(1 for ok, msg, acc in results if not ok)

    # Strictly 1 claim can ever succeed
    assert success_count == 1
    assert fail_count == 4
    for ok, msg, acc in results:
        if not ok:
            assert "already claimed" in msg.lower() or "alredy clamed" in msg.lower()
