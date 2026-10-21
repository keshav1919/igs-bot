"""
Outlook & Hotmail inventory and purchasing service.
Manages email credentials, stock availability, wallet deductions,
and auto-restocking.
"""
import asyncio
import logging
import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.outlook_account import OutlookAccount
from indo_selling_bot.services.wallet_service import WalletService

logger = logging.getLogger("outlook_service")

class OutlookService:
    _lock = asyncio.Lock()
    PRICE_PER_ACCOUNT: float = 3.0

    @classmethod
    async def get_available_stock_count(cls, db: Optional[AsyncSession] = None) -> int:
        """Returns total number of unsold Outlook accounts."""
        if db is not None:
            stmt = select(func.count(OutlookAccount.id)).where(OutlookAccount.is_sold == False)
            return (await db.execute(stmt)).scalar_one() or 0

        async with AsyncSessionLocal() as session:
            stmt = select(func.count(OutlookAccount.id)).where(OutlookAccount.is_sold == False)
            return (await session.execute(stmt)).scalar_one() or 0

    @classmethod
    async def get_sold_stock_count(cls) -> int:
        """Returns total number of sold Outlook accounts."""
        async with AsyncSessionLocal() as session:
            stmt = select(func.count(OutlookAccount.id)).where(OutlookAccount.is_sold == True)
            return (await session.execute(stmt)).scalar_one() or 0

    @classmethod
    async def get_user_accounts(
        cls,
        user_id: int,
        page: int = 1,
        page_size: int = 8
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Returns paginated list of accounts owned by a user."""
        async with AsyncSessionLocal() as session:
            count_stmt = select(func.count(OutlookAccount.id)).where(OutlookAccount.user_id == user_id)
            total_count = (await session.execute(count_stmt)).scalar_one() or 0

            offset = max(0, (page - 1) * page_size)
            stmt = (
                select(OutlookAccount)
                .where(OutlookAccount.user_id == user_id)
                .order_by(OutlookAccount.id.desc())
                .offset(offset)
                .limit(page_size)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [acc.to_dict() for acc in rows], total_count

    @classmethod
    async def get_account_by_id(cls, account_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetch account belonging to user_id."""
        async with AsyncSessionLocal() as session:
            stmt = select(OutlookAccount).where(
                and_(OutlookAccount.id == account_id, OutlookAccount.user_id == user_id)
            )
            acc = (await session.execute(stmt)).scalar_one_or_none()
            return acc.to_dict() if acc else None

    @classmethod
    async def buy_outlook_account(
        cls,
        user_id: int,
        price: float = 3.0
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Atomically purchases an Outlook account using the user's unified wallet.
        Deducts price from wallet, marks account sold, and assigns to user.
        """
        async with cls._lock:
            async with AsyncSessionLocal() as session:
                # 1. Check user balance
                balance = await WalletService.get_balance(session, user_id)
                if balance < price:
                    return False, "INSUFFICIENT_BALANCE", None

                # 2. Select oldest unsold account
                stmt = (
                    select(OutlookAccount)
                    .where(OutlookAccount.is_sold == False)
                    .order_by(OutlookAccount.id.asc())
                    .limit(1)
                )
                account = (await session.execute(stmt)).scalar_one_or_none()
                if not account:
                    return False, "OUT_OF_STOCK", None

                # 3. Deduct balance from unified wallet
                deduct_ok, deduct_msg, remaining = await WalletService.deduct_for_purchase(
                    db=session,
                    telegram_user_id=user_id,
                    amount=int(price),
                    order_code=f"OUTLOOK-ORD-{account.id}",
                    item_title=f"Outlook Account ({account.email})"
                )
                if not deduct_ok:
                    return False, f"Wallet deduction failed: {deduct_msg}", None

                # 4. Mark account sold
                account.user_id = user_id
                account.is_sold = True
                account.price = price
                account.sold_at = datetime.now(timezone.utc)
                await session.commit()
                await session.refresh(account)

                logger.info(f"User {user_id} successfully bought Outlook {account.email} for ₹{price}")
                return True, "SUCCESS", account.to_dict()

    @classmethod
    async def add_stock_bulk(
        cls,
        creds: List[Dict[str, Any]],
        price: float = 3.0
    ) -> Tuple[int, int]:
        """Bulk adds Outlook credentials to stock."""
        added = 0
        skipped = 0

        async with AsyncSessionLocal() as session:
            for c in creds:
                email = c.get("email", "").strip()
                if not email:
                    skipped += 1
                    continue

                # Check if identical unsold account exists
                stmt = select(OutlookAccount).where(
                    and_(OutlookAccount.email == email, OutlookAccount.is_sold == False)
                )
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing:
                    skipped += 1
                    continue

                new_acc = OutlookAccount(
                    email=email,
                    password=c.get("password", ""),
                    refresh_token=c.get("refresh_token", ""),
                    client_id=c.get("client_id", ""),
                    recovery_email=c.get("recovery_email"),
                    is_sold=False,
                    price=price,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(new_acc)
                added += 1

            await session.commit()
        return added, skipped

    @classmethod
    async def restock_account(cls, account_id: int, user_id: int) -> bool:
        """Removes account from user's list and returns it back to unsold stock."""
        async with AsyncSessionLocal() as session:
            stmt = select(OutlookAccount).where(
                and_(OutlookAccount.id == account_id, OutlookAccount.user_id == user_id)
            )
            acc = (await session.execute(stmt)).scalar_one_or_none()
            if not acc:
                return False

            acc.user_id = None
            acc.is_sold = False
            acc.sold_at = None
            await session.commit()
            return True

    @classmethod
    async def update_account_last_code(cls, account_id: int, code: str) -> None:
        """Updates the cached verification code for an account."""
        async with AsyncSessionLocal() as session:
            stmt = select(OutlookAccount).where(OutlookAccount.id == account_id)
            acc = (await session.execute(stmt)).scalar_one_or_none()
            if acc:
                acc.last_code = code
                acc.last_checked_at = datetime.now(timezone.utc)
                await session.commit()

    @classmethod
    async def seed_from_source_db_if_empty(cls) -> int:
        """
        Seeds Outlook accounts from the external outlook bot.db if database is currently empty.
        """
        count = await cls.get_available_stock_count()
        if count > 0:
            return 0

        source_db = Path(r"C:\Users\kesha\Downloads\outlook-bot-intigration\bot.db")
        if not source_db.exists():
            return 0

        try:
            conn = sqlite3.connect(str(source_db))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT email, password, refresh_token, client_id, recovery_email, price "
                "FROM accounts WHERE is_sold = 0;"
            )
            rows = cur.fetchall()
            conn.close()

            creds = [
                {
                    "email": r["email"],
                    "password": r["password"],
                    "refresh_token": r["refresh_token"],
                    "client_id": r["client_id"],
                    "recovery_email": r["recovery_email"],
                }
                for r in rows
            ]
            added, _ = await cls.add_stock_bulk(creds, price=cls.PRICE_PER_ACCOUNT)
            logger.info(f"Seeded {added} Outlook accounts from {source_db}")
            return added
        except Exception as e:
            logger.warning(f"Could not seed from outlook bot.db: {e}")
            return 0
