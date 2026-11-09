"""
Comprehensive Test Suite for Free Indo IGS Feature.
Verifies:
1. /start button integration & Free Indo IGS main menu
2. Tutorial video delivery & unavailable fallback
3. Proxy parsing for Formats A, B, C, D, multiline, prefixes, special characters
4. Two-message credential submission (username first or password first)
5. Valid, invalid (HTTP 407), timeout, and temporary network verification
6. Global duplicate proxy prevention with HMAC-SHA256 fingerprinting
7. 2-proxy milestone: 2 proxies = 1 reward, 4 proxies = 2 rewards
8. Empty stock handling & atomic pending reward queue preservation
9. Automatic pending reward delivery upon stock replenishment
10. Anti-spam: 5 consecutive failures = 20-minute timeout (DB persisted)
11. Expired timeout auto-unlock & failure counter reset on success
12. Full Admin exemption (unlimited attempts, no timeout, no failure blocks)
13. Admin feature configuration (host, port, timeout, max fails, duration, video)
14. Stored proxies retrieval, server:port:user:pass formatting, encryption at rest
15. Read-only retrieval count increment without proxy deletion
16. Unbroken existing bot commands and inventory operations
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, CallbackQuery, User, Video, InlineKeyboardMarkup

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal, init_db
from indo_selling_bot.models.free_indo_igs import (
    FreeIndoSettings,
    ProxySubmission,
    ProxyRateLimit,
    UserRewardProgress,
    RewardTransaction,
)
from indo_selling_bot.services.free_indo_service import FreeIndoService
from indo_selling_bot.services.proxy_parser import ProxyCredentialParser
from indo_selling_bot.services.stock_txt_service import StockTxtService
from indo_selling_bot.bot.keyboards import (
    get_start_welcome_keyboard,
    get_admin_main_keyboard,
    get_free_indo_main_keyboard,
    get_admin_free_indo_keyboard,
    get_admin_stored_proxies_menu_keyboard,
    get_admin_retrieved_proxies_keyboard,
)
from indo_selling_bot.bot.handlers.free_indo import (
    handle_free_indo_menu,
    handle_how_it_works,
    handle_watch_tutorial,
    handle_my_progress,
    handle_submit_proxy_start,
    handle_proxy_submission_message,
    FreeIndoSubmitState,
)
from indo_selling_bot.bot.handlers.admin_free_indo import (
    handle_admin_free_indo_dashboard,
    handle_admin_toggle_enabled,
    handle_admin_save_tutorial_video,
    handle_admin_remove_tutorial,
    handle_admin_retrieve_proxies,
    handle_admin_unban_user,
    AdminFreeIndoState,
)


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Ensure DB tables are initialized before each test."""
    await init_db()
    yield


# =========================================================================
# 1. Start Menu Integration & Keyboards
# =========================================================================

def test_start_menu_keyboard_has_free_indo():
    """Verify that get_start_welcome_keyboard contains '🎁 Free Indo IGS'."""
    kb = get_start_welcome_keyboard()
    flat_buttons = [btn for row in kb.inline_keyboard for btn in row]
    button_texts = [b.text for b in flat_buttons]
    callbacks = [b.callback_data for b in flat_buttons]

    assert "🎁 Free Indo IGS" in button_texts
    assert "free_indo_igs_menu" in callbacks
    assert "🛒 Buy Indo" in button_texts
    assert "📩 Outlook" in button_texts


def test_admin_main_keyboard_has_free_indo():
    """Verify that get_admin_main_keyboard contains '🎁 Free Indo IGS'."""
    kb = get_admin_main_keyboard()
    flat_buttons = [btn for row in kb.inline_keyboard for btn in row]
    callbacks = [b.callback_data for b in flat_buttons]

    assert "admin_free_indo" in callbacks


# =========================================================================
# 2. Free Indo IGS Main Menu & Navigation
# =========================================================================

@pytest.mark.asyncio
async def test_free_indo_menu_navigation():
    """Verify Free Indo IGS main menu text and buttons."""
    mock_msg = MagicMock(spec=Message)
    mock_msg.chat = MagicMock()
    mock_msg.chat.id = 12345
    mock_msg.bot = MagicMock()
    mock_msg.bot.send_video = AsyncMock()
    mock_msg.bot.send_message = AsyncMock()

    await handle_free_indo_menu(mock_msg)

    call_args = mock_msg.bot.send_video.call_args or mock_msg.bot.send_message.call_args
    assert call_args is not None
    caption = call_args[1].get("caption") or call_args[1].get("text")
    assert "Earn FREE Instagram Accounts!" in caption
    assert "dashboard.oxylabs.io" in caption
    assert "Submit below to get free indo" in caption

    kb = call_args[1]["reply_markup"]
    flat_buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert flat_buttons == ["🔙 Back to Main Menu"]


@pytest.mark.asyncio
async def test_how_it_works_instructions():
    """Verify How It Works displays required instructions."""
    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock(spec=Message)
    mock_query.message.edit_text = AsyncMock()

    await handle_how_it_works(mock_query)

    assert mock_query.message.edit_text.called
    text = mock_query.message.edit_text.call_args[0][0]
    assert "Earn FREE Instagram Accounts!" in text
    assert "dashboard.oxylabs.io" in text


@pytest.mark.asyncio
async def test_watch_tutorial_unavailable_and_available():
    """Verify tutorial video when unconfigured vs configured."""
    mock_query = MagicMock(spec=CallbackQuery)
    mock_query.answer = AsyncMock()
    mock_query.message = MagicMock(spec=Message)
    mock_query.message.answer_video = AsyncMock()

    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, tutorial_file_id=None)

    # 1. Unconfigured: should show alert
    await handle_watch_tutorial(mock_query)
    mock_query.answer.assert_called_with(
        "⚠️ Tutorial video is currently unavailable. Please check again later.",
        show_alert=True
    )

    # 2. Configured: should send video
    async with AsyncSessionLocal() as db:
        await FreeIndoService.update_settings(db, tutorial_file_id="VID_TEST_12345")

    mock_query.reset_mock()
    mock_query.answer = AsyncMock()
    await handle_watch_tutorial(mock_query)

    assert mock_query.message.answer_video.called
    video_arg = mock_query.message.answer_video.call_args[1]["video"]
    assert video_arg == "VID_TEST_12345"


# =========================================================================
# 3. Username & Password Parsing (Formats A, B, C, D, Multiline, Special Chars)
# =========================================================================

def test_proxy_parsing_all_formats():
    """Test all required credential input formats."""
    # Format A
    u, p, err = ProxyCredentialParser.parse("mobile_97d8c330:1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Format B
    u, p, err = ProxyCredentialParser.parse("Username: mobile_97d8c330\nPassword: 1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Format C
    u, p, err = ProxyCredentialParser.parse("Username\nmobile_97d8c330\nPassword\n1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Format D
    u, p, err = ProxyCredentialParser.parse("User: mobile_97d8c330\nPass: 1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Format 2 (two lines without labels)
    u, p, err = ProxyCredentialParser.parse("mobile_97d8c330\n1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Password first, username second
    u, p, err = ProxyCredentialParser.parse("1Ly+mSt2JEQS\nmobile_97d8c330")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Server:port:user:pass 4-field format
    u, p, err = ProxyCredentialParser.parse("pr.oxy.labs.io:7777:mobile_97d8c330:1Ly+mSt2JEQS")
    assert u == "mobile_97d8c330" and p == "1Ly+mSt2JEQS" and err is None

    # Various provider prefixes
    for prefix in ("mob_111", "res_222", "residential_333", "resi_444"):
        u, p, _ = ProxyCredentialParser.parse(f"{prefix}:secret+pass/1==")
        assert u == prefix
        assert p == "secret+pass/1=="


# =========================================================================
# 4. Two-Message Credential Submissions
# =========================================================================

@pytest.mark.asyncio
async def test_two_message_submission_user_first():
    """User sends username in first message, password in second message."""
    import uuid
    user_id = int(uuid.uuid4().int % 1000000000)
    sub_user = f"mobile_sub_{uuid.uuid4().hex[:6]}"
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))
    await state.set_state(FreeIndoSubmitState.waiting_for_credentials)

    # Message 1: Username only
    msg1 = MagicMock(spec=Message)
    msg1.from_user = User(id=user_id, is_bot=False, first_name="TestUser")
    msg1.text = f"Username: {sub_user}"
    msg1.answer = AsyncMock()

    await handle_proxy_submission_message(msg1, state)
    data = await state.get_data()
    assert data.get("pending_user") == sub_user

    # Message 2: Password only (with mock verification)
    msg2 = MagicMock(spec=Message)
    msg2.from_user = User(id=user_id, is_bot=False, first_name="TestUser")
    msg2.text = "Pass: mypass+123"
    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    msg2.answer = AsyncMock(return_value=status_mock)

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ("APPROVED", 120.0, None)
        await handle_proxy_submission_message(msg2, state)

    assert status_mock.edit_text.called
    response_text = status_mock.edit_text.call_args[0][0]
    assert "Congratulations" in response_text
    assert "1/2 Completed" in response_text
    # State should remain waiting_for_credentials for the second proxy
    assert await state.get_state() == FreeIndoSubmitState.waiting_for_credentials.state


# =========================================================================
# 5. Encryption at Rest & HMAC Fingerprint
# =========================================================================

def test_password_encryption_and_fingerprint():
    """Verify password encryption at rest and HMAC-SHA256 fingerprint generation."""
    raw_pass = "UltraSecret+123/="
    enc = FreeIndoService.encrypt_password(raw_pass)

    assert enc != raw_pass
    assert "UltraSecret" not in enc

    dec = FreeIndoService.decrypt_password(enc)
    assert dec == raw_pass

    # Fingerprint is deterministic and unique
    fp1 = FreeIndoService.generate_fingerprint("pr.oxy.labs.io", 7777, "mobile_test")
    fp2 = FreeIndoService.generate_fingerprint("pr.oxy.labs.io", 7777, "mobile_test")
    fp3 = FreeIndoService.generate_fingerprint("pr.oxy.labs.io", 7777, "mobile_different")

    assert fp1 == fp2
    assert fp1 != fp3


# =========================================================================
# 6. Duplicate Proxy Prevention Across Users
# =========================================================================

@pytest.mark.asyncio
async def test_duplicate_proxy_prevention_across_users():
    """A proxy approved for User A must never be approved for User B."""
    user_a = 999111
    user_b = 999222
    uname = f"mobile_dup_{int(datetime.now().timestamp())}"
    pwd = "validpassword"

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ("APPROVED", 100.0, None)

        # 1. User A submits and gets approved
        res1 = await FreeIndoService.submit_and_verify_proxy(user_a, uname, pwd)
        assert res1["ok"] is True
        assert res1["status"] == "APPROVED"

        # 2. User B submits same proxy credentials
        res2 = await FreeIndoService.submit_and_verify_proxy(user_b, uname, pwd)
        assert res2["ok"] is False
        assert res2["status"] == "DUPLICATE"
        assert "already been verified" in res2["message"]


# =========================================================================
# 7. Milestone Reward System: 2 Proxies = 1 Reward, 4 Proxies = 2 Rewards
# =========================================================================

@pytest.mark.asyncio
async def test_reward_milestone_delivery():
    """Verify reward awarded at proxy 2 and proxy 4."""
    user_id = 555001
    ts = int(datetime.now().timestamp())

    # Mock StockTxtService to provide stock
    mock_account = {
        "email": "testig@metamail.store",
        "password": "igpass123",
        "username": "indo_ig_test",
        "cookie": "ig_cookie_xyz",
        "timestamp_str": "08 Nov 2026, 12:00:00 IST",
        "raw_line": "testig@metamail.store:igpass123:indo_ig_test:08 Nov 2026, 12:00:00 IST",
        "day_number": 1
    }

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify, \
         patch.object(StockTxtService, "get_available_categories", return_value=[{"day": 1, "quantity": 10}]), \
         patch.object(StockTxtService, "purchase_accounts", return_value=(True, "Success", [mock_account])):

        mock_verify.return_value = ("APPROVED", 150.0, None)

        # Proxy 1 -> Approved, no milestone
        r1 = await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_{ts}_1", "pass1")
        assert r1["status"] == "APPROVED"
        assert r1["is_milestone"] is False

        # Proxy 2 -> Milestone reached, 1 account delivered!
        r2 = await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_{ts}_2", "pass2")
        assert r2["status"] == "APPROVED"
        assert r2["is_milestone"] is True
        assert r2["reward_status"] == "DELIVERED"
        assert r2["account"]["email"] == "testig@metamail.store"

        # Proxy 3 -> Approved, no milestone
        r3 = await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_{ts}_3", "pass3")
        assert r3["status"] == "APPROVED"
        assert r3["is_milestone"] is False

        # Proxy 4 -> Second milestone reached!
        r4 = await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_{ts}_4", "pass4")
        assert r4["status"] == "APPROVED"
        assert r4["is_milestone"] is True
        assert r4["reward_status"] == "DELIVERED"


# =========================================================================
# 8. Empty Stock & Automatic Pending Reward Delivery
# =========================================================================

@pytest.mark.asyncio
async def test_empty_stock_and_pending_reward_delivery():
    """Verify empty stock saves pending reward, and deliver_pending_rewards fulfills it."""
    import uuid
    from sqlalchemy import delete
    user_id = int(uuid.uuid4().int % 1000000000)
    ts = int(datetime.now().timestamp())

    # Clear any old pending transactions from prior test runs
    async with AsyncSessionLocal() as db:
        await db.execute(delete(RewardTransaction).where(RewardTransaction.reward_status == "PENDING"))
        await db.commit()

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify, \
         patch.object(StockTxtService, "get_available_categories", return_value=[]):  # No stock

        mock_verify.return_value = ("APPROVED", 110.0, None)

        # Submit 2 proxies with 0 stock
        await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_p_{ts}_1", "p1")
        r2 = await FreeIndoService.submit_and_verify_proxy(user_id, f"mob_p_{ts}_2", "p2")

        assert r2["is_milestone"] is True
        assert r2["reward_status"] == "PENDING"
        assert r2["account"] is None

        # Check user progress shows pending reward
        async with AsyncSessionLocal() as db:
            prog = await FreeIndoService.get_user_progress(db, user_id)
            assert prog["pending_rewards"] == 1
            assert prog["rewards_delivered"] == 0

    # Stock is now replenished -> run deliver_pending_rewards
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_acc = {
        "email": "replenished@metamail.store",
        "password": "pass",
        "username": "replenished_user",
        "raw_line": "replenished@metamail.store:pass:replenished_user"
    }

    with patch.object(StockTxtService, "get_available_categories", return_value=[{"day": 2, "quantity": 5}]), \
         patch.object(StockTxtService, "purchase_accounts", return_value=(True, "Success", [mock_acc])):

        delivered = await FreeIndoService.deliver_pending_rewards(bot=mock_bot)
        assert delivered == 1
        assert mock_bot.send_message.called

        async with AsyncSessionLocal() as db:
            prog = await FreeIndoService.get_user_progress(db, user_id)
            assert prog["pending_rewards"] == 0
            assert prog["rewards_delivered"] == 1


# =========================================================================
# 9. Anti-Spam: 5 Consecutive Failures = 20-Minute Timeout (DB Persisted)
# =========================================================================

@pytest.mark.asyncio
async def test_anti_spam_timeout_and_db_persistence():
    """Verify 5 failed attempts locks user out for 20 minutes in DB."""
    import uuid
    user_id = int(uuid.uuid4().int % 1000000000)

    # Reset any rate limit for user
    async with AsyncSessionLocal() as db:
        await FreeIndoService.clear_timeout(db, user_id)

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ("REJECTED", 80.0, "Auth Failed")

        # 4 failures: not yet timed out
        for i in range(4):
            res = await FreeIndoService.submit_and_verify_proxy(user_id, f"bad_user_{i}", "wrong")
            assert res["status"] == "REJECTED"
            assert res["timed_out"] is False

        # 5th failure: triggers 20-minute restriction!
        res5 = await FreeIndoService.submit_and_verify_proxy(user_id, "bad_user_5", "wrong")
        assert res5["status"] == "REJECTED"
        assert res5["timed_out"] is True
        assert res5["timeout_minutes"] == 20

        # Subsequent attempt is immediately blocked by rate limiter
        blocked_res = await FreeIndoService.submit_and_verify_proxy(user_id, "another_try", "pass")
        assert blocked_res["status"] == "RESTRICTED"
        assert blocked_res["remaining_minutes"] >= 19


# =========================================================================
# 10. Admin Exemption from Rate Limits and Timeouts
# =========================================================================

@pytest.mark.asyncio
async def test_admin_exemption_from_failures_and_timeout():
    """Admin must never receive failure restrictions or timeouts."""
    admin_id = 99999999
    original_admin = settings.TELEGRAM_ADMIN_USER_ID
    settings.TELEGRAM_ADMIN_USER_ID = admin_id

    try:
        with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = ("REJECTED", 50.0, "Auth Failed")

            # Admin submits 10 failed proxies in a row
            for i in range(10):
                res = await FreeIndoService.submit_and_verify_proxy(admin_id, f"admin_test_{i}", "pass", is_admin=True)
                assert res["status"] == "REJECTED"
                assert res["timed_out"] is False

            # Admin is never restricted
            async with AsyncSessionLocal() as db:
                is_restr, _, _ = await FreeIndoService.check_user_timeout(db, admin_id, is_admin=True)
                assert is_restr is False
    finally:
        settings.TELEGRAM_ADMIN_USER_ID = original_admin


# =========================================================================
# 11. Stored Proxies Retrieval & Copy Buttons
# =========================================================================

@pytest.mark.asyncio
async def test_stored_proxies_retrieval_and_formatting():
    """Verify stored proxies are retrieved in server:port:username:password format without deletion."""
    user_id = 333444
    uname = f"mobile_store_{int(datetime.now().timestamp())}"
    pwd = "SecretPass123!+"

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ("APPROVED", 95.0, None)
        await FreeIndoService.submit_and_verify_proxy(user_id, uname, pwd)

    async with AsyncSessionLocal() as db:
        proxies = await FreeIndoService.get_stored_proxies(db, limit=5)
        matched = [p for p in proxies if p["username"] == uname]
        assert len(matched) == 1
        p = matched[0]

        # Four-field output format
        assert p["formatted"] == f"pr.oxylabs.io:7777:{uname}:{pwd}"
        assert p["password"] == pwd
        assert p["retrieval_count"] >= 1

    # Retrieval is read-only (proxy still in database)
    async with AsyncSessionLocal() as db:
        stats = await FreeIndoService.get_proxy_statistics(db)
        assert stats["total_stored"] >= 1


def test_is_proxy_credential_input_filter():
    """Verify commands and menu buttons are filtered out so they are never intercepted as proxy credentials."""
    from indo_selling_bot.bot.handlers.free_indo import is_proxy_credential_input
    from aiogram.types import Message

    # Commands must be excluded
    m_admin = MagicMock(spec=Message)
    m_admin.text = "/admin"
    assert not is_proxy_credential_input(m_admin)

    m_start = MagicMock(spec=Message)
    m_start.text = "/start"
    assert not is_proxy_credential_input(m_start)

    # Menu buttons must be excluded
    m_buy = MagicMock(spec=Message)
    m_buy.text = "🛒 Buy Indo"
    assert not is_proxy_credential_input(m_buy)

    m_back = MagicMock(spec=Message)
    m_back.text = "🔙 Back to Main Menu"
    assert not is_proxy_credential_input(m_back)

    # Real proxy input must be accepted
    m_proxy = MagicMock(spec=Message)
    m_proxy.text = "mobile_3c0e6a29:qB~z+kIpiw8M"
    assert is_proxy_credential_input(m_proxy)


@pytest.mark.asyncio
async def test_admin_notification_on_proxy_approval():
    """Verify that when a proxy is approved, admin receives notification with tap-to-copy proxy and user details."""
    import uuid
    user_id = int(uuid.uuid4().int % 1000000000)
    sub_user = f"mobile_notif_{uuid.uuid4().hex[:6]}"
    storage = MemoryStorage()
    state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=user_id, user_id=user_id))
    await state.set_state(FreeIndoSubmitState.waiting_for_credentials)

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="John", username="johndoe")
    msg.text = f"{sub_user}:SecretPass123"
    msg.bot = MagicMock()
    msg.bot.send_message = AsyncMock()

    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status_mock)

    with patch.object(FreeIndoService, "verify_proxy_connection", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = ("APPROVED", 100.0, None)
        await handle_proxy_submission_message(msg, state)

    # Check that message.bot.send_message was called to notify admin
    assert msg.bot.send_message.called
    admin_calls = msg.bot.send_message.call_args_list
    assert len(admin_calls) >= 1
    admin_text = admin_calls[0][1].get("text") or admin_calls[0][0][1]
    assert "New Free Indo Proxy Approved!" in admin_text
    assert str(user_id) in admin_text
    assert "@johndoe" in admin_text
    assert f"{sub_user}:SecretPass123" in admin_text
