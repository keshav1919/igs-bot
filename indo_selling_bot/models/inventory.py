"""
Inventory items, day-based groupings, and audit change logs.
"""
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import String, Text, Integer, Float, Boolean, DateTime, ForeignKey, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from indo_selling_bot.database import Base

if TYPE_CHECKING:
    from indo_selling_bot.models.category import Category
    from indo_selling_bot.models.order import OrderItem

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Day-based grouping (e.g., 'Day 1' through 'Day 10', or batch/maturation age)
    day_group: Mapped[str] = mapped_column(String(32), default="Day 1", index=True, nullable=False)

    price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stock_alert_threshold: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    # Listing status: 'active', 'paused', 'archived', 'out_of_stock'
    status: Mapped[str] = mapped_column(String(32), default="active", index=True, nullable=False)

    # Fulfillment mode and secure payload (released ONLY upon verified payment)
    fulfillment_type: Mapped[str] = mapped_column(String(32), default="digital_asset", nullable=False)
    fulfillment_payload: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Internal admin notes (hidden from customers)
    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    # Relationships
    category: Mapped["Category"] = relationship("Category", back_populates="items", lazy="joined")
    change_logs: Mapped[list["InventoryChangeLog"]] = relationship(
        "InventoryChangeLog",
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="desc(InventoryChangeLog.created_at)",
        lazy="selectin"
    )
    order_items: Mapped[list["OrderItem"]] = relationship("OrderItem", back_populates="inventory_item", lazy="selectin")

    @property
    def is_available(self) -> bool:
        return self.status == "active" and self.quantity > 0

    @property
    def is_low_stock(self) -> bool:
        return self.quantity > 0 and self.quantity <= self.stock_alert_threshold

    def get_age_days(self, reference_time: datetime | None = None) -> int:
        """
        Calculate exact age in completed days (24-hour periods) from creation timestamp.
        Handles timezones and protects against future timestamps.
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        created = self.created_at
        if not created:
            return 0
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        diff = ref - created
        total_seconds = max(0, int(diff.total_seconds()))
        return total_seconds // 86400

    def get_day_group(self, reference_time: datetime | None = None) -> str:
        """
        Derive legitimate day-based grouping (Day 1 through Day 10) dynamically from timestamp.
        Day 1: < 24 hours (0 days completed)
        Day 2: 24h to < 48 hours (1 day completed)
        ...
        Day 10: 216h to < 240 hours (9 days completed)
        Day 10+: >= 240 hours (10+ days completed)
        """
        days = self.get_age_days(reference_time)
        day_num = days + 1
        if day_num <= 10:
            return f"Day {day_num}"
        return "Day 10+"

    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_inventory_quantity_non_negative"),
    )

    def get_day_number(self, reference_time: datetime | None = None) -> int:
        """
        Derive 1-indexed day number (1 for <24h / 0 days elapsed, 2 for 24-48h / 1 day elapsed, etc.).
        """
        return self.get_age_days(reference_time) + 1

    def get_age_category_label(self, reference_time: datetime | None = None) -> str:
        """
        Human-readable category label: '1 Day Old', '2 Days Old', ... '20 Days Old', or '20+ Days Old'.
        """
        day = self.get_day_number(reference_time)
        if day == 1:
            return "1 Day Old"
        elif day <= 20:
            return f"{day} Days Old"
        return "20+ Days Old"

    @property
    def age_days(self) -> int:
        """Dynamic age in completed 24-hour days."""
        return self.get_age_days()

    @property
    def calculated_day_group(self) -> str:
        """Dynamic day group property calculated from timestamp."""
        return self.get_day_group()

    def __repr__(self) -> str:
        return f"<InventoryItem(id={self.id}, sku='{self.sku}', title='{self.title}', qty={self.quantity}, status='{self.status}')>"


class InventoryChangeLog(Base):
    """Complete audit trail for all changes to inventory items."""
    __tablename__ = "inventory_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True, index=True)
    item_sku: Mapped[str] = mapped_column(String(64), nullable=False)
    item_title: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # 'CREATE', 'UPDATE_STOCK', 'UPDATE_DETAILS', 'CSV_IMPORT', 'ORDER_DEDUCT', 'RESTOCK', 'DELETE'
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_user: Mapped[str] = mapped_column(String(64), default="SYSTEM", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    item: Mapped[InventoryItem | None] = relationship("InventoryItem", back_populates="change_logs")

    def __repr__(self) -> str:
        return f"<InventoryChangeLog(id={self.id}, sku='{self.item_sku}', action='{self.action}', by='{self.admin_user}')>"
