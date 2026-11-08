"""
Free Indo IGS Database Models.
Handles proxy submissions, rate limits, user reward progress, reward transactions, and feature settings.
"""
from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, Text, Integer, Float, Boolean, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column
from indo_selling_bot.database import Base


class FreeIndoSettings(Base):
    __tablename__ = "free_indo_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    proxy_host: Mapped[str] = mapped_column(String(128), default="pr.oxy.labs.io", nullable=False)
    proxy_port: Mapped[int] = mapped_column(Integer, default=7777, nullable=False)
    connection_timeout: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    max_failed_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    timeout_minutes: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    tutorial_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<FreeIndoSettings(enabled={self.enabled}, host={self.proxy_host}:{self.proxy_port})>"


class ProxySubmission(Base):
    __tablename__ = "proxy_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    credential_fingerprint: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    proxy_host: Mapped[str] = mapped_column(String(128), default="pr.oxy.labs.io", nullable=False)
    proxy_port: Mapped[int] = mapped_column(Integer, default=7777, nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Verification status: 'APPROVED', 'REJECTED', 'TIMEOUT', 'TEMPORARY_ERROR'
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    response_time_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrieval_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        # Enforce unique approved proxy fingerprint globally across all users
        Index(
            "uq_approved_proxy_fingerprint",
            "credential_fingerprint",
            unique=True,
            sqlite_where=(status == "APPROVED")
        ),
    )

    def __repr__(self) -> str:
        return f"<ProxySubmission(id={self.id}, user_id={self.telegram_user_id}, status={self.status})>"


class ProxyRateLimit(Base):
    __tablename__ = "proxy_rate_limits"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    timeout_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<ProxyRateLimit(user_id={self.telegram_user_id}, failures={self.consecutive_failures}, timeout={self.timeout_until})>"


class UserRewardProgress(Base):
    __tablename__ = "user_reward_progress"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True)
    approved_proxy_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rewards_earned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rewards_delivered: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pending_rewards: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    def __repr__(self) -> str:
        return f"<UserRewardProgress(user_id={self.telegram_user_id}, approved={self.approved_proxy_count}, delivered={self.rewards_delivered}, pending={self.pending_rewards})>"


class RewardTransaction(Base):
    __tablename__ = "reward_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    # Status: 'DELIVERED', 'PENDING', 'FAILED'
    reward_status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True, nullable=False)
    account_email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    account_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<RewardTransaction(id={self.id}, user_id={self.telegram_user_id}, status={self.reward_status})>"
