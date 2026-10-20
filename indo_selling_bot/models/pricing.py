"""
Category pricing models and price change audit logs.
Allows changing prices per age category anytime without modifying underlying inventory records.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Float, Boolean, BigInteger, DateTime, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from indo_selling_bot.database import Base

class CategoryPrice(Base):
    __tablename__ = "category_prices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day_number: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    category_label: Mapped[str] = mapped_column(String(64), nullable=False)
    price: Mapped[float] = mapped_column(Float, default=25000.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    allow_free: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_by_admin: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    __table_args__ = (
        CheckConstraint("day_number >= 1 AND day_number <= 20", name="ck_category_price_day_range"),
        CheckConstraint("price >= 0", name="ck_category_price_non_negative"),
    )

    def __repr__(self) -> str:
        return f"<CategoryPrice(day={self.day_number}, price={self.price} {self.currency})>"


class PriceChangeLog(Base):
    __tablename__ = "price_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day_number: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    old_price: Mapped[float] = mapped_column(Float, nullable=False)
    new_price: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    admin_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<PriceChangeLog(day={self.day_number}, {self.old_price}->{self.new_price} by {self.admin_id})>"
