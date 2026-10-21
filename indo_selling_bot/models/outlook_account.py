"""
OutlookAccount database model for managing Outlook/Hotmail inventory stock,
ownership, and verification codes.
"""
from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, Text, DateTime, Boolean, Float, Integer
from sqlalchemy.orm import Mapped, mapped_column
from indo_selling_bot.database import Base

class OutlookAccount(Base):
    __tablename__ = "outlook_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(BigInteger, index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    password: Mapped[str] = mapped_column(String(256), nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recovery_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_sold: Mapped[bool] = mapped_column(Boolean, default=False, index=True, nullable=False)
    price: Mapped[float] = mapped_column(Float, default=3.0, nullable=False)
    sold_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "password": self.password,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "recovery_email": self.recovery_email,
            "is_sold": self.is_sold,
            "price": self.price,
            "sold_at": self.sold_at,
            "last_code": self.last_code,
            "last_checked_at": self.last_checked_at,
            "created_at": self.created_at,
        }
