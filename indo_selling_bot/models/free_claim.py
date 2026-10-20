"""
FreeClaim database model to enforce strictly 1 free Indo IG per user.
Uses telegram_user_id as Primary Key to guarantee physical uniqueness.
"""
from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from indo_selling_bot.database import Base

class FreeClaim(Base):
    __tablename__ = "free_claims"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    account_email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<FreeClaim(user_id={self.telegram_user_id}, email={self.account_email})>"
