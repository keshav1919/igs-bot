"""
Order processing service with verified payment workflow, idempotency protection, and fulfillment release.
"""
import uuid
import secrets
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from indo_selling_bot.models.order import Order, OrderItem, OrderStatus
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.inventory_service import InventoryService

class OrderService:
    @staticmethod
    def generate_order_code() -> str:
        """Generate a clean, collision-free order code (e.g., ORD-20261019-7A8B)."""
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        rand_str = secrets.token_hex(3).upper()
        return f"ORD-{date_str}-{rand_str}"

    @staticmethod
    async def create_order(
        db: AsyncSession,
        telegram_user_id: int,
        item_id: int,
        quantity: int = 1,
        telegram_username: str | None = None,
        telegram_first_name: str | None = None,
        payment_method: str = "MANUAL_TRANSFER"
    ) -> tuple[Order | None, str]:
        """
        Create a new order for a Telegram customer.
        Validates inventory stock, reserves stock atomically, and generates order code.
        Returns: (order, error_message)
        """
        if quantity <= 0:
            return None, "Quantity must be at least 1."

        # Fetch inventory item
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return None, "Listing not found or unavailable."

        if item.status != "active":
            return None, f"Item is currently {item.status.replace('_', ' ')}."

        if item.quantity < quantity:
            return None, f"Insufficient stock. Available: {item.quantity} units."

        # Prevent immediate duplicate spam: check if user has an identical pending order in the last 60 seconds
        recent_cutoff = datetime.now(timezone.utc).timestamp() - 60
        stmt = (
            select(Order)
            .where(
                Order.telegram_user_id == telegram_user_id,
                Order.status == OrderStatus.PENDING_PAYMENT,
                Order.total_amount == float(item.price * quantity)
            )
            .order_by(desc(Order.created_at))
            .limit(1)
        )
        recent_order = (await db.execute(stmt)).scalar_one_or_none()
        if recent_order and recent_order.created_at.timestamp() > recent_cutoff:
            return recent_order, "Existing pending order found. Please submit your payment reference or wait before placing a duplicate."

        # Atomically deduct stock
        deducted = await InventoryService.deduct_stock(
            db=db,
            item_id=item.id,
            quantity=quantity,
            admin_user=f"TG:{telegram_user_id}",
            notes=f"Order reservation for user {telegram_user_id}"
        )
        if not deducted:
            return None, "Failed to reserve inventory stock."

        # Create Order
        order_code = OrderService.generate_order_code()
        subtotal = float(item.price * quantity)

        order = Order(
            order_code=order_code,
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            telegram_first_name=telegram_first_name,
            status=OrderStatus.PENDING_PAYMENT,
            total_amount=subtotal,
            currency=item.currency,
            payment_method=payment_method,
            fulfillment_released=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(order)
        await db.flush()

        order_item = OrderItem(
            order_id=order.id,
            inventory_item_id=item.id,
            item_title=item.title,
            item_sku=item.sku,
            unit_price=item.price,
            quantity=quantity,
            subtotal=subtotal,
            day_group=item.day_group,
            released_payload=None  # will be populated ONLY upon verified payment
        )
        db.add(order_item)
        await db.flush()

        return order, ""

    @staticmethod
    async def get_by_code(db: AsyncSession, order_code: str) -> Order | None:
        """Fetch order by unique code with related items."""
        stmt = (
            select(Order)
            .where(Order.order_code == order_code.strip())
            .options(selectinload(Order.items).selectinload(OrderItem.inventory_item))
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_id(db: AsyncSession, order_id: int) -> Order | None:
        """Fetch order by primary key with related items."""
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items).selectinload(OrderItem.inventory_item))
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_customer_orders(
        db: AsyncSession,
        telegram_user_id: int,
        limit: int = 10
    ) -> list[Order]:
        """Fetch orders belonging exclusively to the given Telegram user (access isolation)."""
        stmt = (
            select(Order)
            .where(Order.telegram_user_id == telegram_user_id)
            .options(selectinload(Order.items))
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def submit_payment_reference(
        db: AsyncSession,
        order_code: str,
        telegram_user_id: int,
        tx_reference: str,
        note: str | None = None
    ) -> tuple[bool, str]:
        """
        Record customer's payment reference.
        Enforces user ownership, prevents duplicate tx submissions, moves status to PAYMENT_SUBMITTED.
        Note: Customer screenshots are never automatically treated as verified confirmation.
        """
        order = await OrderService.get_by_code(db, order_code)
        if not order:
            return False, "Order not found."

        if order.telegram_user_id != telegram_user_id:
            return False, "Unauthorized: You do not have permission to view or modify this order."

        if order.status in (OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED):
            return False, "Order has already been verified and fulfilled."

        if order.status == OrderStatus.CANCELLED:
            return False, "Cannot submit payment for a cancelled order."

        # Check if tx_reference was already used in another verified order to prevent double-spending
        clean_ref = tx_reference.strip()
        ref_check_stmt = select(Order).where(
            Order.tx_reference == clean_ref,
            Order.id != order.id,
            Order.status.in_([OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED])
        )
        existing_paid = (await db.execute(ref_check_stmt)).scalar_one_or_none()
        if existing_paid:
            return False, "This transaction reference has already been verified for another order."

        order.tx_reference = clean_ref
        order.payment_proof_note = note.strip() if note else None
        order.payment_submitted_at = datetime.now(timezone.utc)
        order.status = OrderStatus.PAYMENT_SUBMITTED
        order.updated_at = datetime.now(timezone.utc)

        await db.flush()
        return True, "Payment reference submitted successfully. An administrator will verify and release your order."

    @staticmethod
    async def verify_and_fulfill(
        db: AsyncSession,
        order_id: int,
        admin_user: str,
        verification_notes: str | None = None
    ) -> tuple[bool, str, Order | None]:
        """
        Authorized verification workflow:
        1. Confirms order is pending/submitted.
        2. Prevents duplicate fulfillment (atomic check).
        3. Releases deliverable payloads safely.
        4. Marks order PAID_VERIFIED.
        5. Logs action in AuditLog.
        """
        order = await OrderService.get_by_id(db, order_id)
        if not order:
            return False, "Order not found", None

        # Guard against duplicate fulfillment
        if order.fulfillment_released or order.status in (OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED):
            return False, "Order has already been verified and fulfilled.", order

        if order.status == OrderStatus.CANCELLED:
            return False, "Cannot verify a cancelled order.", order

        # Assemble fulfillment payloads for items
        fulfillment_lines = []
        for item_rel in order.items:
            inv = item_rel.inventory_item
            if inv and inv.fulfillment_payload:
                item_rel.released_payload = inv.fulfillment_payload
                fulfillment_lines.append(f"📦 {inv.title} [{inv.day_group}]:\n{inv.fulfillment_payload}")
            else:
                fulfillment_lines.append(f"📦 {item_rel.item_title}: [Service/Physical Delivery confirmed by Admin]")

        order.status = OrderStatus.PAID_VERIFIED
        order.fulfillment_released = True
        order.verified_by_admin = admin_user
        order.verified_at = datetime.now(timezone.utc)
        order.verification_notes = verification_notes
        order.fulfillment_data = "\n\n".join(fulfillment_lines)
        order.fulfillment_sent_at = datetime.now(timezone.utc)
        order.updated_at = datetime.now(timezone.utc)

        # Audit log
        audit = AuditLog(
            action="VERIFY_PAYMENT",
            performed_by=admin_user,
            details=f"Order {order.order_code} verified. Tx: {order.tx_reference}. Amount: {order.total_amount} {order.currency}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit)
        await db.flush()

        return True, "Payment verified and fulfillment released successfully.", order

    @staticmethod
    async def reject_payment(
        db: AsyncSession,
        order_id: int,
        admin_user: str,
        reason: str | None = None,
        restock: bool = True
    ) -> tuple[bool, str]:
        """Reject unverified payment and optionally return reserved stock to inventory."""
        order = await OrderService.get_by_id(db, order_id)
        if not order:
            return False, "Order not found"

        if order.fulfillment_released:
            return False, "Cannot reject an order whose fulfillment has already been released."

        old_status = order.status
        order.status = OrderStatus.REJECTED
        order.verification_notes = reason or "Payment proof rejected by admin."
        order.verified_by_admin = admin_user
        order.updated_at = datetime.now(timezone.utc)

        if restock:
            for order_item in order.items:
                await InventoryService.restock_item(
                    db=db,
                    item_id=order_item.inventory_item_id,
                    quantity=order_item.quantity,
                    admin_user=admin_user,
                    notes=f"Restock from rejected order {order.order_code}"
                )

        audit = AuditLog(
            action="REJECT_PAYMENT",
            performed_by=admin_user,
            details=f"Order {order.order_code} rejected. Reason: {reason}. Stock restocked: {restock}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(audit)
        await db.flush()

        return True, "Order payment rejected and inventory restocked."

    @staticmethod
    async def list_orders(
        db: AsyncSession,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
        per_page: int = 20
    ) -> tuple[list[Order], int, int]:
        """List orders with pagination, search by order code or tx_reference, and status filter."""
        stmt = select(Order).options(selectinload(Order.items))
        count_stmt = select(func.count(Order.id))

        filters = []
        if status:
            filters.append(Order.status == status)
        if search:
            search_pat = f"%{search.strip()}%"
            filters.append(
                (Order.order_code.ilike(search_pat)) |
                (Order.tx_reference.ilike(search_pat)) |
                (Order.telegram_username.ilike(search_pat))
            )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        stmt = stmt.order_by(desc(Order.created_at))

        total_count = (await db.execute(count_stmt)).scalar_one() or 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)
        stmt = stmt.offset(offset).limit(per_page)

        result = await db.execute(stmt)
        orders = list(result.scalars().all())
        return orders, total_count, total_pages
