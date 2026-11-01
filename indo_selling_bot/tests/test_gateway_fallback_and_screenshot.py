"""
Tests for Payment Gateway Fallback and Admin Screenshot Verification.
Verifies:
1. Normal flow: When TrueX Gateway succeeds, dynamic QR is generated with inline buttons and auto-detection.
2. Fallback flow: When TrueX Gateway fails (timeout/API error), falls back to self-generated UPI QR
   with NO inline buttons (reply_markup=None) and instructions to send screenshot.
3. Customer screenshot upload: Validates photo/document submission, updates status to PENDING_REVIEW,
   and forwards to admins with review keyboard.
4. Admin verification: Admin approval credits wallet and notifies user; admin rejection leaves wallet unchanged.
"""
import pytest
import uuid
import random
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import Message, User, Chat, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db, AsyncSessionLocal
from indo_selling_bot.models.wallet import Customer, Wallet, DepositRequest
from indo_selling_bot.services.wallet_service import WalletService
from indo_selling_bot.bot.handlers.add_fund import (
    AddFundState,
    process_deposit_amount,
    handle_payment_screenshot_photo,
    handle_payment_screenshot_document,
    handle_admin_approve_payment,
    handle_admin_reject_payment,
)

ADMIN_ID = list(settings.admin_ids)[0]


@pytest.fixture(autouse=True)
async def setup_db():
    await init_db()


def _make_msg(user_id: int, text: str = "", username: str = "testuser") -> AsyncMock:
    fake_user = User(id=user_id, is_bot=False, first_name="Test", username=username)
    fake_chat = Chat(id=user_id, type="private")
    msg = AsyncMock(spec=Message)
    msg.from_user = fake_user
    msg.chat = fake_chat
    msg.text = text
    msg.message_id = random.randint(1000, 9999)
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    msg.bot = AsyncMock()
    msg.bot.send_message = AsyncMock()
    msg.bot.send_photo = AsyncMock()

    status_mock = AsyncMock()
    status_mock.delete = AsyncMock()
    msg.answer.return_value = status_mock

    qr_msg_mock = AsyncMock()
    qr_msg_mock.message_id = random.randint(10000, 99999)
    qr_msg_mock.chat = fake_chat
    msg.answer_photo.return_value = qr_msg_mock
    return msg


def _make_fsm(user_id: int) -> FSMContext:
    storage = MemoryStorage()
    key = StorageKey(bot_id=12345, chat_id=user_id, user_id=user_id)
    return FSMContext(storage=storage, key=key)


@pytest.mark.asyncio
async def test_gateway_success_has_inline_buttons_and_polling():
    """When TrueXGateway succeeds, dynamic QR has inline buttons and polling starts."""
    cust_id = random.randint(100000000, 999999999)
    msg = _make_msg(cust_id, text="50")
    state = _make_fsm(cust_id)
    await state.set_state(AddFundState.waiting_for_amount)

    fake_order_data = {
        "orderId": f"TXG-{uuid.uuid4().hex[:8]}",
        "qrUrl": "https://example.com/qr.png",
        "upiIntent": "upi://pay?pa=test@upi",
    }
    fake_qr_bytes = b"\x89PNG\r\n\x1a\n" + b"fake_png_data"

    with patch("indo_selling_bot.bot.handlers.add_fund.TrueXGatewayService.create_order", new=AsyncMock(return_value=fake_order_data)), \
         patch("indo_selling_bot.bot.handlers.add_fund.TrueXGatewayService.fetch_qr_image_bytes", new=AsyncMock(return_value=fake_qr_bytes)), \
         patch("indo_selling_bot.bot.handlers.add_fund.polling_manager.start_polling", new=AsyncMock()) as mock_start_polling:

        await process_deposit_amount(msg, state)

        # Verify answer_photo was called with inline buttons (reply_markup is not None)
        assert msg.answer_photo.called
        kwargs = msg.answer_photo.call_args.kwargs
        assert kwargs.get("reply_markup") is not None
        # Verify polling started
        assert mock_start_polling.called


@pytest.mark.asyncio
async def test_gateway_failure_falls_back_to_self_qr_no_buttons():
    """
    When TrueXGateway fails (raises exception / API problem), falls back to self-generated UPI QR,
    has NO inline buttons (reply_markup=None), and prompts to send payment screenshot.
    """
    cust_id = random.randint(100000000, 999999999)
    msg = _make_msg(cust_id, text="50")
    state = _make_fsm(cust_id)
    await state.set_state(AddFundState.waiting_for_amount)

    # Simulate gateway network/API failure
    with patch("indo_selling_bot.bot.handlers.add_fund.TrueXGatewayService.create_order", new=AsyncMock(side_effect=Exception("Read timed out"))), \
         patch("indo_selling_bot.bot.handlers.add_fund.polling_manager.start_polling", new=AsyncMock()) as mock_start_polling:

        await process_deposit_amount(msg, state)

        # Verify polling did NOT start
        assert not mock_start_polling.called

        # Verify answer_photo was called with reply_markup=None (NO inline buttons)
        assert msg.answer_photo.called
        kwargs = msg.answer_photo.call_args.kwargs
        assert kwargs.get("reply_markup") is None

        # Verify caption contains instructions to send screenshot
        caption = kwargs.get("caption", "")
        assert "Send payment screenshot" in caption
        assert "UPI ID:" in caption

        # Verify state is set to waiting_for_screenshot
        current_state = await state.get_state()
        assert current_state == AddFundState.waiting_for_screenshot.state

        state_data = await state.get_data()
        assert "deposit_ref" in state_data
        assert state_data["amount"] == 50.0


@pytest.mark.asyncio
async def test_customer_screenshot_upload_and_admin_review():
    """Verify customer screenshot submission, admin notification, and review keyboard."""
    cust_id = random.randint(100000000, 999999999)
    deposit_ref = f"DEP-TEST-{uuid.uuid4().hex[:8].upper()}"

    # 1. Create a pending deposit
    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, cust_id, "testuser_scr")
        dep = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=cust_id,
            amount=50.0,
            reference=deposit_ref,
            initial_status="PENDING_PAYMENT"
        )
        await db.commit()

    # 2. Simulate customer sending photo
    msg = _make_msg(cust_id)
    photo_mock = MagicMock()
    photo_mock.file_id = f"AgACAgUAAxkBAAI_test_{uuid.uuid4().hex[:6]}"
    msg.photo = [photo_mock]

    state = _make_fsm(cust_id)
    await state.set_state(AddFundState.waiting_for_screenshot)
    await state.update_data(deposit_ref=deposit_ref, amount=50.0)

    await handle_payment_screenshot_photo(msg, state)

    # Customer received acknowledgement
    assert msg.answer.called
    customer_ack = msg.answer.call_args[0][0]
    assert "Payment screenshot received" in customer_ack
    assert "awaiting admin verification" in customer_ack

    # Deposit status updated to PENDING_REVIEW
    async with AsyncSessionLocal() as db:
        updated_dep = await WalletService.get_deposit_by_ref(db, deposit_ref)
        assert updated_dep.status == "PENDING_REVIEW"
        assert updated_dep.screenshot_file_id == photo_mock.file_id

    # Admin was alerted with review photo and keyboard
    assert msg.bot.send_photo.called
    admin_call_kwargs = msg.bot.send_photo.call_args.kwargs
    assert admin_call_kwargs.get("chat_id") in settings.admin_ids
    assert admin_call_kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_customer_screenshot_document_upload():
    """Verify customer sending screenshot as image document also triggers review workflow."""
    cust_id = random.randint(100000000, 999999999)
    deposit_ref = f"DEP-DOC-{uuid.uuid4().hex[:8].upper()}"

    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, cust_id, "testuser_doc")
        dep = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=cust_id,
            amount=60.0,
            reference=deposit_ref,
            initial_status="PENDING_PAYMENT"
        )
        await db.commit()

    msg = _make_msg(cust_id)
    doc_mock = MagicMock()
    doc_mock.file_id = f"AgACAgUAAxkBAAI_doc_{uuid.uuid4().hex[:6]}"
    doc_mock.mime_type = "image/png"
    msg.document = doc_mock

    state = _make_fsm(cust_id)
    await state.set_state(AddFundState.waiting_for_screenshot)
    await state.update_data(deposit_ref=deposit_ref, amount=60.0)

    await handle_payment_screenshot_document(msg, state)

    # Customer received acknowledgement
    assert msg.answer.called
    customer_ack = msg.answer.call_args[0][0]
    assert "Payment screenshot received" in customer_ack

    # Deposit status updated to PENDING_REVIEW
    async with AsyncSessionLocal() as db:
        updated_dep = await WalletService.get_deposit_by_ref(db, deposit_ref)
        assert updated_dep.status == "PENDING_REVIEW"
        assert updated_dep.screenshot_file_id == doc_mock.file_id


@pytest.mark.asyncio
async def test_admin_approve_payment():
    """Verify admin approval credits customer wallet and notifies user."""
    cust_id = random.randint(100000000, 999999999)
    deposit_ref = f"DEP-APP-{uuid.uuid4().hex[:8].upper()}"

    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, cust_id, "testuser_app")
        dep = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=cust_id,
            amount=100.0,
            reference=deposit_ref,
            initial_status="PENDING_PAYMENT"
        )
        await WalletService.attach_screenshot(db, dep.reference, "fake_file_id")
        await db.commit()

    # Create mock callback query from admin
    query = AsyncMock(spec=CallbackQuery)
    query.data = f"adm_app_pay:{deposit_ref}"
    admin_user = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="admin")
    query.from_user = admin_user
    query.message = AsyncMock()
    query.message.edit_caption = AsyncMock()
    query.answer = AsyncMock()
    query.bot = AsyncMock()
    query.bot.send_message = AsyncMock()

    await handle_admin_approve_payment(query)

    # Verify query answered successfully
    assert query.answer.called

    # Verify customer wallet credited
    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, cust_id)
        assert bal == 100.0
        dep_db = await WalletService.get_deposit_by_ref(db, deposit_ref)
        assert dep_db.status == "APPROVED"

    # Verify customer was notified
    assert query.bot.send_message.called
    sent_text = query.bot.send_message.call_args.kwargs["text"]
    assert "Payment Approved" in sent_text
    assert "100.00" in sent_text


@pytest.mark.asyncio
async def test_admin_reject_payment():
    """Verify admin rejection does not credit wallet and notifies user."""
    cust_id = random.randint(100000000, 999999999)
    deposit_ref = f"DEP-REJ-{uuid.uuid4().hex[:8].upper()}"

    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, cust_id, "testuser_rej")
        dep = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=cust_id,
            amount=75.0,
            reference=deposit_ref,
            initial_status="PENDING_PAYMENT"
        )
        await WalletService.attach_screenshot(db, dep.reference, "fake_file_id")
        await db.commit()

    query = AsyncMock(spec=CallbackQuery)
    query.data = f"adm_rej_pay:{deposit_ref}"
    admin_user = User(id=ADMIN_ID, is_bot=False, first_name="Admin", username="admin")
    query.from_user = admin_user
    query.message = AsyncMock()
    query.message.edit_caption = AsyncMock()
    query.answer = AsyncMock()
    query.bot = AsyncMock()
    query.bot.send_message = AsyncMock()

    await handle_admin_reject_payment(query)

    assert query.answer.called

    # Verify wallet remains 0.0
    async with AsyncSessionLocal() as db:
        bal = await WalletService.get_balance(db, cust_id)
        assert bal == 0.0
        dep_db = await WalletService.get_deposit_by_ref(db, deposit_ref)
        assert dep_db.status == "REJECTED"

    # Verify customer notified of rejection
    assert query.bot.send_message.called
    sent_text = query.bot.send_message.call_args.kwargs["text"]
    assert "Rejected" in sent_text


@pytest.mark.asyncio
async def test_customer_screenshot_not_needed_for_gateway_orders():
    """
    When customer pays via dynamic TrueX Gateway QR (txg_order_id present):
    Payment auto-detects, so screenshots DO NOT go to admin approval.
    Bot responds that screenshot is not needed.
    """
    cust_id = random.randint(100000000, 999999999)
    deposit_ref = f"DEP-TXG-{uuid.uuid4().hex[:8].upper()}"

    # 1. Create a dynamic gateway deposit (with txg_order_id)
    async with AsyncSessionLocal() as db:
        await WalletService.get_or_create_customer(db, cust_id, "testuser_txg")
        dep = await WalletService.create_deposit_request(
            db=db,
            telegram_user_id=cust_id,
            amount=50.25,
            reference=deposit_ref,
            txg_order_id="TXG_AUTO_DETECT_12345",
            initial_status="PENDING_PAYMENT"
        )
        await db.commit()

    # 2. Customer sends photo
    msg = _make_msg(cust_id)
    photo_mock = MagicMock()
    photo_mock.file_id = f"AgACAgUAAxkBAAI_txg_{uuid.uuid4().hex[:6]}"
    msg.photo = [photo_mock]

    state = _make_fsm(cust_id)
    await handle_payment_screenshot_photo(msg, state)

    # Bot responds that screenshot is not needed
    assert msg.answer.called
    response_text = msg.answer.call_args[0][0]
    assert "Payment Screenshot Not Needed!" in response_text
    assert "automatically detected" in response_text

    # Verify admin was NOT alerted
    assert not msg.bot.send_photo.called
