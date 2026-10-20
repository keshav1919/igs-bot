"""
Free Indo IG Claim Service.
Guarantees strictly 1 free Indo account per user using DB constraints and concurrency locks.
"""
import asyncio
import logging
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.free_claim import FreeClaim
from indo_selling_bot.services.stock_txt_service import StockTxtService

logger = logging.getLogger("free_claim_service")

class FreeClaimService:
    _lock = asyncio.Lock()

    @classmethod
    async def has_user_claimed(cls, telegram_user_id: int) -> bool:
        """Check if user has already claimed their free Indo IG."""
        async with AsyncSessionLocal() as db:
            stmt = select(FreeClaim).where(FreeClaim.telegram_user_id == telegram_user_id)
            res = (await db.execute(stmt)).scalar_one_or_none()
            return res is not None

    @classmethod
    async def claim_free_ig(
        cls,
        telegram_user_id: int,
        username: str | None = None
    ) -> tuple[bool, str, dict | None]:
        """
        Atomically claim 1 free Indo IG for a user.
        - Verifies user has not claimed previously.
        - Locks process to eliminate race conditions.
        - Withdraws 1 account from stock.txt.
        - Persists claim to free_claims table.
        - If DB integrity error occurs, restores account to stock.txt.
        Returns: (success: bool, message: str, account_dict: dict | None)
        """
        # Quick pre-check before locking
        if await cls.has_user_claimed(telegram_user_id):
            return False, "⚠️ Already claimed!", None

        async with cls._lock:
            # Re-check under lock
            if await cls.has_user_claimed(telegram_user_id):
                return False, "⚠️ Already claimed!", None

            # Find available stock categories
            available_cats = StockTxtService.get_available_categories()
            if not available_cats:
                return False, "❌ Out of stock! Try later.", None

            # Pick category with most stock to preserve diversity
            target_cat = max(available_cats, key=lambda c: c["quantity"])
            day_num = target_cat["day"]

            ok, msg, accounts = StockTxtService.purchase_accounts(
                day_number=day_num,
                quantity=1
            )
            if not ok or not accounts:
                return False, "❌ Out of stock! Try later.", None

            account = accounts[0]

            # Persist claim to database
            try:
                async with AsyncSessionLocal() as db:
                    claim = FreeClaim(
                        telegram_user_id=telegram_user_id,
                        username=username,
                        account_email=account.get("email"),
                        account_raw=account.get("raw_line")
                    )
                    db.add(claim)
                    await db.commit()
            except IntegrityError:
                # Race condition: user already claimed in parallel
                logger.warning(f"IntegrityError for user {telegram_user_id}. Restoring stock line.")
                try:
                    StockTxtService.add_stock_lines(account.get("raw_line", ""))
                except Exception as rest_err:
                    logger.error(f"Failed to restore stock line on claim conflict: {rest_err}")
                return False, "⚠️ Already claimed!", None
            except Exception as e:
                logger.error(f"Database error while recording free claim for user {telegram_user_id}: {e}")
                try:
                    StockTxtService.add_stock_lines(account.get("raw_line", ""))
                except Exception:
                    pass
                return False, f"Database error: {e}", None

            logger.info(
                f"Free Indo account claimed by user {telegram_user_id} ({username}): {account.get('email')}"
            )
            return True, "✅ Free Indo claimed!", account
