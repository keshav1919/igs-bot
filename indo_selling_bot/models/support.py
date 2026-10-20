"""
Customer support inquiries and tickets for the Telegram bot and Admin panel.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Text, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from indo_selling_bot.database import Base

class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticket_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    telegram_user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    telegram_username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    replied_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Status: 'OPEN', 'REPLIED', 'RESOLVED', 'CLOSED'
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True, nullable=False)

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

    def __repr__(self) -> str:
        return f"<SupportTicket(code='{self.ticket_code}', user={self.telegram_user_id}, status='{self.status}')>"
