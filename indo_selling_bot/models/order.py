"""
Order and OrderItem models with verified payment workflow and fulfillment protection.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import String, Text, Integer, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from indo_selling_bot.database import Base

if TYPE_CHECKING:
    from indo_selling_bot.models.inventory import InventoryItem

class OrderStatus:
    PENDING_PAYMENT = "PENDING_PAYMENT"         # Order placed, awaiting customer payment
    PAYMENT_SUBMITTED = "PAYMENT_SUBMITTED"     # Customer submitted tx reference, awaiting admin verification
    PAID_VERIFIED = "PAID_VERIFIED"             # Admin verified payment, goods released/fulfilled
    REJECTED = "REJECTED"                       # Admin rejected payment (invalid tx ref / fraudulent attempt)
    CANCELLED = "CANCELLED"                     # Customer or admin cancelled order
    FULFILLED = "FULFILLED"                     # Completed fulfillment

class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    
    # Telegram customer identity
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_first_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Order financial & status
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.PENDING_PAYMENT, index=True, nullable=False)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)

    # Payment details
    payment_method: Mapped[str] = mapped_column(String(64), default="MANUAL_TRANSFER", nullable=False)
    tx_reference: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    payment_proof_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Verification workflow (Authorized verification requirement)
    verified_by_admin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Fulfillment data (Encrypted or released digital deliverables)
    fulfillment_released: Mapped[bool] = mapped_column(default=False, nullable=False)
    fulfillment_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    fulfillment_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Order(id={self.id}, code='{self.order_code}', user={self.telegram_user_id}, status='{self.status}')>"


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    inventory_item_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True, default=None, index=True)
    
    item_title: Mapped[str] = mapped_column(String(200), nullable=False)
    item_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    day_group: Mapped[str | None] = mapped_column(String(32), nullable=True)
    
    # Specific payload released upon verification
    released_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    order: Mapped[Order] = relationship("Order", back_populates="items")
    inventory_item: Mapped["InventoryItem"] = relationship("InventoryItem", back_populates="order_items", lazy="joined")

    def __repr__(self) -> str:
        return f"<OrderItem(id={self.id}, order_id={self.order_id}, sku='{self.item_sku}', qty={self.quantity})>"
