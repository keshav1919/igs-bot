"""
Pricing service for category-based inventory pricing.
Enforces:
- Dynamic category pricing from Day 1 Old through Day 20 Old.
- Global price configuration for all Indo Igs (Days 1 to infinity).
- Immediate price adjustments without modifying underlying inventory records.
- Zero price rejection unless explicitly enabled for free items.
- Negative price rejection.
- Audit logging of all price changes with admin ID, previous price, new price, and timestamp.
- In-memory pricing cache for high performance.
"""
import time
from datetime import datetime, timezone
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.models.pricing import CategoryPrice, PriceChangeLog
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.models.setting import StoreSetting

class PricingService:
    DEFAULT_CURRENCY = "INR"
    DEFAULT_BASE_PRICE = 7.0

    # In-memory pricing cache for high performance
    _price_cache: dict[int, tuple[float, str]] = {}
    _global_price_cache: float | None = None
    _cache_timestamp: float = 0.0
    CACHE_TTL: float = 300.0

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._price_cache.clear()
        cls._global_price_cache = None
        cls._cache_timestamp = 0.0

    @classmethod
    async def get_global_price(cls, db: AsyncSession) -> float:
        """Fetch the store-wide default price for all Indo Igs (Days 1 to infinity)."""
        now = time.monotonic()
        if cls._global_price_cache is not None and (now - cls._cache_timestamp) < cls.CACHE_TTL:
            return cls._global_price_cache

        stmt = select(StoreSetting).where(StoreSetting.key == "global_indo_price")
        try:
            setting = (await db.execute(stmt)).scalar_one_or_none()
            if setting and setting.value:
                val = float(setting.value)
                if val > 0:
                    cls._global_price_cache = val
                    cls._cache_timestamp = now
                    return val
        except Exception:
            pass
        return cls.DEFAULT_BASE_PRICE

    @classmethod
    async def set_global_price(
        cls,
        db: AsyncSession,
        new_price: float,
        admin_id: int,
        currency: str = DEFAULT_CURRENCY
    ) -> tuple[bool, str]:
        """Set the global price for all Indo Igs (Day 1 to infinity)."""
        if new_price <= 0:
            return False, "Price must be greater than 0."

        cls.invalidate_cache()
        clean_price = float(round(new_price, 2))
        now_utc = datetime.now(timezone.utc)

        stmt = select(StoreSetting).where(StoreSetting.key == "global_indo_price")
        setting = (await db.execute(stmt)).scalar_one_or_none()
        if not setting:
            setting = StoreSetting(
                key="global_indo_price",
                value=str(clean_price),
                description="Global Indo accounts price per unit",
                updated_at=now_utc
            )
            db.add(setting)
        else:
            setting.value = str(clean_price)
            setting.updated_at = now_utc

        # Update all active category_prices to keep all days synchronized
        stmt_cp = select(CategoryPrice)
        all_cp = (await db.execute(stmt_cp)).scalars().all()
        for cp in all_cp:
            cp.price = clean_price
            cp.currency = currency
            cp.updated_at = now_utc
            cp.updated_by_admin = str(admin_id)

        audit = AuditLog(
            action="GLOBAL_PRICE_UPDATED",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=f"Updated global Indo price to ₹{clean_price:,.0f} per account for all days.",
            created_at=now_utc
        )
        db.add(audit)

        await db.commit()
        return True, f"Global price for all Indo Igs successfully updated to ₹{clean_price:,.0f}."

    @classmethod
    async def get_category_price(cls, db: AsyncSession, day_number: int) -> CategoryPrice | None:
        """Fetch the pricing record for a specific day category."""
        stmt = select(CategoryPrice).where(CategoryPrice.day_number == day_number)
        return (await db.execute(stmt)).scalar_one_or_none()

    @classmethod
    async def get_effective_price(
        cls,
        db: AsyncSession,
        day_number: int,
        fallback_price: float | None = None,
        fallback_currency: str = DEFAULT_CURRENCY
    ) -> tuple[float, str]:
        """
        Get the effective unit price and currency for a category.
        Uses in-memory cache to avoid repeated SQL queries on frequent button taps.
        """
        now = time.monotonic()
        if day_number in cls._price_cache and (now - cls._cache_timestamp) < cls.CACHE_TTL:
            return cls._price_cache[day_number]

        rec = await cls.get_category_price(db, day_number)
        if rec and rec.is_active:
            res = (float(rec.price), rec.currency)
            cls._price_cache[day_number] = res
            return res

        # Check if store_settings has configured global price
        stmt = select(StoreSetting).where(StoreSetting.key == "global_indo_price")
        try:
            setting = (await db.execute(stmt)).scalar_one_or_none()
            if setting and setting.value:
                val = float(setting.value)
                if val > 0:
                    res = (val, fallback_currency)
                    cls._price_cache[day_number] = res
                    return res
        except Exception:
            pass

        if fallback_price is not None:
            res = (float(fallback_price), fallback_currency)
            cls._price_cache[day_number] = res
            return res

        res = (cls.DEFAULT_BASE_PRICE, fallback_currency)
        cls._price_cache[day_number] = res
        return res

    @classmethod
    async def get_all_category_prices(cls, db: AsyncSession, max_days: int = 20) -> list[dict]:
        """
        Get the price for every category from Day 1 to Day max_days.
        Returns a list of dicts with day, label, price, currency, updated_at, updated_by.
        """
        stmt = select(CategoryPrice).order_by(CategoryPrice.day_number)
        existing = {cp.day_number: cp for cp in (await db.execute(stmt)).scalars().all()}
        global_price = await cls.get_global_price(db)

        results = []
        for day in range(1, max_days + 1):
            day_label = f"Day {day} Old"
            if day in existing:
                cp = existing[day]
                results.append({
                    "day": day,
                    "label": day_label,
                    "price": float(cp.price),
                    "currency": cp.currency,
                    "allow_free": cp.allow_free,
                    "is_active": cp.is_active,
                    "updated_at": cp.updated_at,
                    "updated_by": cp.updated_by_admin,
                    "is_custom": True
                })
            else:
                results.append({
                    "day": day,
                    "label": day_label,
                    "price": global_price,
                    "currency": cls.DEFAULT_CURRENCY,
                    "allow_free": False,
                    "is_active": True,
                    "updated_at": None,
                    "updated_by": None,
                    "is_custom": False
                })
        return results

    @classmethod
    async def set_category_price(
        cls,
        db: AsyncSession,
        day_number: int,
        new_price: float,
        admin_id: int,
        currency: str = DEFAULT_CURRENCY,
        allow_free: bool = False,
        reason: str | None = None
    ) -> tuple[bool, str, CategoryPrice | None]:
        """
        Update or initialize the price for a specific day category.
        """
        if not (1 <= day_number <= 20):
            return False, f"Invalid day category {day_number}. Must be between 1 and 20.", None

        if new_price < 0:
            return False, "Price cannot be negative. Please enter a valid positive amount.", None

        if new_price == 0 and not allow_free:
            return False, "Price cannot be zero unless free items are explicitly enabled.", None

        cls.invalidate_cache()
        now_utc = datetime.now(timezone.utc)
        clean_price = float(round(new_price, 2))

        stmt = select(CategoryPrice).where(CategoryPrice.day_number == day_number)
        cp = (await db.execute(stmt)).scalar_one_or_none()

        old_price = float(cp.price) if cp else await cls.get_global_price(db)
        day_label = f"Day {day_number} Old"

        if not cp:
            cp = CategoryPrice(
                day_number=day_number,
                category_label=day_label,
                price=clean_price,
                currency=currency,
                allow_free=allow_free,
                is_active=True,
                updated_by_admin=str(admin_id),
                updated_at=now_utc
            )
            db.add(cp)
        else:
            cp.price = clean_price
            cp.currency = currency
            cp.allow_free = allow_free
            cp.is_active = True
            cp.updated_by_admin = str(admin_id)
            cp.updated_at = now_utc

        change_log = PriceChangeLog(
            day_number=day_number,
            old_price=old_price,
            new_price=clean_price,
            currency=currency,
            admin_id=admin_id,
            reason=reason or f"Admin {admin_id} updated price for {day_label}",
            created_at=now_utc
        )
        db.add(change_log)

        audit = AuditLog(
            action="PRICE_UPDATED",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=(
                f"Updated price for {day_label} from {old_price:,.0f} {currency} "
                f"to {clean_price:,.0f} {currency}. Free enabled: {allow_free}."
            ),
            created_at=now_utc
        )
        db.add(audit)

        await db.commit()
        await db.refresh(cp)
        return True, f"Successfully updated {day_label} price to {currency} {clean_price:,.0f}.", cp

    @classmethod
    async def get_price_history(
        cls,
        db: AsyncSession,
        day_number: int | None = None,
        limit: int = 20
    ) -> list[PriceChangeLog]:
        """Fetch audit log history of price changes."""
        stmt = select(PriceChangeLog).order_by(desc(PriceChangeLog.created_at))
        if day_number is not None:
            stmt = stmt.where(PriceChangeLog.day_number == day_number)
        stmt = stmt.limit(limit)
        return list((await db.execute(stmt)).scalars().all())
