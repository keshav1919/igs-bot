"""
Unit tests for Orders, Payment Verification workflow, customer isolation, and duplicate prevention.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.services.order_service import OrderService
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.payment_service import PaymentService
from indo_selling_bot.models.order import OrderStatus

@pytest.mark.asyncio
async def test_order_creation_and_stock_reservation(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    initial_stock = item.quantity

    order, err = await OrderService.create_order(
        db=seeded_db,
        telegram_user_id=12345678,
        item_id=item.id,
        quantity=2,
        telegram_username="customer_one",
        telegram_first_name="Alice"
    )
    await seeded_db.commit()

    assert err == ""
    assert order is not None
    assert order.order_code.startswith("ORD-")
    assert order.status == OrderStatus.PENDING_PAYMENT
    assert order.total_amount == 100000.0
    assert order.fulfillment_released is False

    # Stock should be decremented immediately to prevent overselling
    refetched_item = await InventoryService.get_by_id(seeded_db, item.id)
    assert refetched_item.quantity == initial_stock - 2

@pytest.mark.asyncio
async def test_customer_isolation_and_payment_submission(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    order, _ = await OrderService.create_order(
        db=seeded_db,
        telegram_user_id=11111,
        item_id=item.id,
        quantity=1
    )
    await seeded_db.commit()

    # Customer 99999 attempts to submit payment reference for Customer 11111's order -> Unauthorized!
    success_unauthorized, msg_unauth = await OrderService.submit_payment_reference(
        db=seeded_db,
        order_code=order.order_code,
        telegram_user_id=99999,
        tx_reference="BCA-ATTACK-001"
    )
    assert success_unauthorized is False
    assert "Unauthorized" in msg_unauth

    # Legitimate customer 11111 submits payment reference
    success_ok, msg_ok = await OrderService.submit_payment_reference(
        db=seeded_db,
        order_code=order.order_code,
        telegram_user_id=11111,
        tx_reference="BCA-TRANSFER-VALID-9988"
    )
    await seeded_db.commit()
    assert success_ok is True

    # Order status must now be PAYMENT_SUBMITTED (awaiting admin review)
    refetched = await OrderService.get_by_code(seeded_db, order.order_code)
    assert refetched.status == OrderStatus.PAYMENT_SUBMITTED
    assert refetched.tx_reference == "BCA-TRANSFER-VALID-9988"

@pytest.mark.asyncio
async def test_authorized_verification_and_fulfillment_release(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    order, _ = await OrderService.create_order(
        db=seeded_db,
        telegram_user_id=22222,
        item_id=item.id,
        quantity=1
    )
    await OrderService.submit_payment_reference(
        db=seeded_db,
        order_code=order.order_code,
        telegram_user_id=22222,
        tx_reference="USDT-TXID-77665544332211"
    )
    await seeded_db.commit()

    # Admin verifies payment
    success, msg, verified_order = await OrderService.verify_and_fulfill(
        db=seeded_db,
        order_id=order.id,
        admin_user="superadmin_bob",
        verification_notes="Verified on TRON explorer."
    )
    await seeded_db.commit()

    assert success is True
    assert verified_order.status == OrderStatus.PAID_VERIFIED
    assert verified_order.fulfillment_released is True
    assert verified_order.verified_by_admin == "superadmin_bob"
    assert "KEY-ABCD-1122-3344" in verified_order.fulfillment_data

    # Duplicate fulfillment check: Attempting to verify again must fail!
    dup_success, dup_msg, _ = await OrderService.verify_and_fulfill(
        db=seeded_db,
        order_id=order.id,
        admin_user="superadmin_bob"
    )
    assert dup_success is False
    assert "already been verified" in dup_msg

@pytest.mark.asyncio
async def test_reject_payment_and_restock(seeded_db: AsyncSession):
    item = await InventoryService.get_by_sku(seeded_db, "TEST-LIC-D1")
    stock_before = item.quantity

    order, _ = await OrderService.create_order(
        db=seeded_db,
        telegram_user_id=33333,
        item_id=item.id,
        quantity=2
    )
    await OrderService.submit_payment_reference(
        db=seeded_db,
        order_code=order.order_code,
        telegram_user_id=33333,
        tx_reference="FAKE-RECEIPT-REF-000"
    )
    await seeded_db.commit()

    # Admin rejects payment proof
    success, msg = await OrderService.reject_payment(
        db=seeded_db,
        order_id=order.id,
        admin_user="admin_charlie",
        reason="TxID not found in banking logs",
        restock=True
    )
    await seeded_db.commit()

    assert success is True
    refetched_order = await OrderService.get_by_id(seeded_db, order.id)
    assert refetched_order.status == OrderStatus.REJECTED

    # Inventory must have been automatically restocked
    refetched_item = await InventoryService.get_by_id(seeded_db, item.id)
    assert refetched_item.quantity == stock_before

@pytest.mark.asyncio
async def test_payment_instructions_formatting(seeded_db: AsyncSession):
    text = await PaymentService.format_instructions_for_telegram(
        db=seeded_db,
        order_code="ORD-TEST-999",
        total_amount=50000.0,
        currency="IDR"
    )
    assert "ORD-TEST-999" in text
    assert "50,000.00 IDR" in text
    assert "IMPORTANT SECURITY NOTICE" in text
    assert "Screenshots are not automatically verified" in text
