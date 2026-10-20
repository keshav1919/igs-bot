"""
Stock import history tracking model.
Maintains persistent audit logs for all batch TXT imports performed by administrators.
"""
from datetime import datetime, timezone
from sqlalchemy import String, Integer, BigInteger, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from indo_selling_bot.database import Base

class StockImportHistory(Base):
    __tablename__ = "stock_import_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    total_lines: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    valid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="CONFIRMED", nullable=False)  # 'CONFIRMED', 'CANCELLED', 'ROLLED_BACK'
    summary_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    def __repr__(self) -> str:
        return f"<StockImportHistory(id={self.id}, file='{self.filename}', valid={self.valid_count}, status='{self.status}')>"
