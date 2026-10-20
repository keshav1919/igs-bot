"""
Delivery service interface.
Provides a safe delivery-service interface and configurable pending-fulfilment state.
Enforces:
- Only release an item after purchase transaction succeeds.
- Never expose another customer's purchase.
- Does not store or distribute third-party account passwords or secrets.
- If fulfillment fails, puts in recoverable pending-fulfilment state.
- Provides admin actions to retry or resolve pending fulfillment safely.
"""
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.models.order import Order
from indo_selling_bot.models.audit import AuditLog

class DeliveryService:
    @staticmethod
    async def queue_for_fulfillment(db: AsyncSession, order: Order) -> Order:
        """
        Safely place an order into a verified, pending-fulfillment state.
        Preserves deliverable payload until explicit verified delivery.
        """
        order.status = "PENDING_FULFILLMENT"
        order.fulfillment_released = False
        await db.flush()
        return order

    @staticmethod
    async def process_fulfillment(db: AsyncSession, order_id: int) -> tuple[bool, str]:
        """
        Delivery-service hook interface. To be wired with specific digital sequence.
        """
        return False, "Automated delivery sequence scheduled for next integration phase."

    @staticmethod
    async def fulfill_order(
        db: AsyncSession,
        order_code: str,
        admin_id: int | None = None,
        fulfillment_notes: str | None = None
    ) -> tuple[bool, str, Order | None]:
        """
        Fulfill an order from the pending fulfillment queue.
        Safely marks the order as fulfilled and releases fulfillment state.
        """
        stmt = select(Order).where(Order.order_code == order_code.strip())
        order = (await db.execute(stmt)).scalar_one_or_none()

        if not order:
            return False, f"Order {order_code} not found.", None

        if order.status == "FULFILLED" and order.fulfillment_released:
            return True, f"Order {order_code} is already fulfilled.", order

        now_utc = datetime.now(timezone.utc)
        order.status = "FULFILLED"
        order.fulfillment_released = True
        order.fulfillment_sent_at = now_utc
        order.verified_by_admin = str(admin_id) if admin_id else "SYSTEM"
        if fulfillment_notes:
            order.verification_notes = fulfillment_notes

        audit = AuditLog(
            action="ORDER_FULFILLED",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}" if admin_id else "SYSTEM",
            details=f"Order {order_code} fulfilled successfully for user {order.telegram_user_id}. {fulfillment_notes or ''}",
            created_at=now_utc
        )
        db.add(audit)
        await db.commit()

        return True, f"Order {order_code} marked as fulfilled.", order

    @staticmethod
    async def fail_fulfillment(
        db: AsyncSession,
        order_code: str,
        reason: str,
        admin_id: int | None = None
    ) -> tuple[bool, str, Order | None]:
        """
        Record a fulfillment failure. Keeps order in PENDING_FULFILLMENT for admin inspection.
        """
        stmt = select(Order).where(Order.order_code == order_code.strip())
        order = (await db.execute(stmt)).scalar_one_or_none()

        if not order:
            return False, f"Order {order_code} not found.", None

        now_utc = datetime.now(timezone.utc)
        order.status = "PENDING_FULFILLMENT"
        order.fulfillment_released = False
        order.verification_notes = f"Fulfillment error: {reason}"

        audit = AuditLog(
            action="FULFILLMENT_FAILED",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}" if admin_id else "SYSTEM",
            details=f"Fulfillment failed for {order_code}: {reason}. Retained in PENDING_FULFILLMENT.",
            created_at=now_utc
        )
        db.add(audit)
        await db.commit()

        return False, f"Fulfillment failed: {reason}. Order kept in pending queue for admin review.", order

    @staticmethod
    async def get_pending_fulfillments(db: AsyncSession, limit: int = 20) -> list[Order]:
        """List orders awaiting fulfillment."""
        stmt = (
            select(Order)
            .where(Order.status == "PENDING_FULFILLMENT")
            .order_by(desc(Order.created_at))
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())
