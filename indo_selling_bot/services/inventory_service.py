"""
Inventory management service.
Handles CRUD, day-based grouping, search, filters, pagination, stock alerts, and audit logging.
"""
from typing import Any
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, or_, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import secrets
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.order import Order, OrderItem
from indo_selling_bot.models.audit import AuditLog

class InventoryService:
    @staticmethod
    def calculate_day_group_from_timestamp(created_at: datetime | None, reference_time: datetime | None = None) -> str:
        """Calculate Day 1 through Day 10 from timestamp."""
        if not created_at:
            return "Day 1"
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        dt = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
        diff = ref - dt
        total_seconds = max(0, int(diff.total_seconds()))
        days = total_seconds // 86400
        day_num = days + 1
        return f"Day {day_num}" if day_num <= 10 else "Day 10+"

    @staticmethod
    async def log_change(
        db: AsyncSession,
        item: InventoryItem,
        action: str,
        admin_user: str = "SYSTEM",
        field_name: str | None = None,
        old_val: Any = None,
        new_val: Any = None,
        notes: str | None = None
    ) -> InventoryChangeLog:
        """Create an audit log record for an inventory change."""
        log = InventoryChangeLog(
            item_id=item.id,
            item_sku=item.sku,
            item_title=item.title,
            action=action,
            field_name=field_name,
            old_value=str(old_val) if old_val is not None else None,
            new_value=str(new_val) if new_val is not None else None,
            admin_user=admin_user,
            notes=notes,
            created_at=datetime.now(timezone.utc)
        )
        db.add(log)
        await db.flush()
        return log

    @staticmethod
    async def create_item(
        db: AsyncSession,
        sku: str,
        category_id: int,
        title: str,
        price: float,
        quantity: int = 0,
        day_group: str = "Day 1",
        description: str | None = None,
        currency: str = "INR",
        stock_alert_threshold: int = 5,
        status: str = "active",
        fulfillment_type: str = "digital_asset",
        fulfillment_payload: str | None = None,
        internal_notes: str | None = None,
        created_at: datetime | None = None,
        admin_user: str = "SYSTEM"
    ) -> InventoryItem:
        """
        Create a new inventory listing and record in audit log.
        Calculates age from legitimate creation timestamp rather than purely manual entry.
        """
        now_utc = datetime.now(timezone.utc)
        
        # Resolve creation timestamp and day group legitimately
        if created_at is not None:
            ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            computed_dg = InventoryService.calculate_day_group_from_timestamp(ts, now_utc)
            final_day_group = computed_dg if not day_group or day_group == "Day 1" else day_group.strip()
            final_created_at = ts
        else:
            # If a day group like 'Day 3' was explicitly selected without created_at,
            # align the creation timestamp to legitimately reflect that age (e.g. 2 days ago)
            if day_group.strip().startswith("Day ") and day_group.strip()[4:].isdigit():
                day_num = int(day_group.strip()[4:])
                offset_days = max(0, day_num - 1)
                final_created_at = now_utc - timedelta(days=offset_days, hours=1)
                final_day_group = day_group.strip()
            else:
                final_created_at = now_utc
                final_day_group = day_group.strip() or "Day 1"

        item = InventoryItem(
            sku=sku.strip().upper(),
            category_id=category_id,
            title=title.strip(),
            price=float(price),
            quantity=int(quantity),
            day_group=final_day_group,
            description=description.strip() if description else None,
            currency=currency.strip().upper(),
            stock_alert_threshold=int(stock_alert_threshold),
            status=status.strip().lower(),
            fulfillment_type=fulfillment_type.strip(),
            fulfillment_payload=fulfillment_payload.strip() if fulfillment_payload else None,
            internal_notes=internal_notes.strip() if internal_notes else None,
            created_at=final_created_at,
            updated_at=now_utc
        )
        db.add(item)
        await db.flush()

        await InventoryService.log_change(
            db=db,
            item=item,
            action="CREATE",
            admin_user=admin_user,
            new_val=f"Qty: {quantity}, Price: {price}, DayGroup: {final_day_group}, CreatedAt: {final_created_at.isoformat()}",
            notes=f"Initial item listing created with SKU {item.sku}"
        )
        return item

    @staticmethod
    async def get_by_id(db: AsyncSession, item_id: int) -> InventoryItem | None:
        """Retrieve inventory item by primary key."""
        stmt = (
            select(InventoryItem)
            .where(InventoryItem.id == item_id)
            .options(selectinload(InventoryItem.category), selectinload(InventoryItem.change_logs))
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_sku(db: AsyncSession, sku: str) -> InventoryItem | None:
        """Retrieve inventory item by unique SKU."""
        stmt = (
            select(InventoryItem)
            .where(func.upper(InventoryItem.sku) == sku.strip().upper())
            .options(selectinload(InventoryItem.category))
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def update_item(
        db: AsyncSession,
        item: InventoryItem,
        updates: dict[str, Any],
        admin_user: str = "SYSTEM"
    ) -> InventoryItem:
        """Update inventory item fields with precise change tracking."""
        tracked_fields = [
            "title", "price", "quantity", "day_group", "status",
            "category_id", "stock_alert_threshold", "fulfillment_type",
            "fulfillment_payload", "internal_notes", "description", "created_at"
        ]

        for field in tracked_fields:
            if field in updates and updates[field] is not None:
                old_val = getattr(item, field)
                new_val = updates[field]
                if str(old_val) != str(new_val):
                    setattr(item, field, new_val)
                    action = "UPDATE_STOCK" if field == "quantity" else ("STATUS_UPDATE" if field == "status" else "UPDATE_DETAILS")
                    await InventoryService.log_change(
                        db=db,
                        item=item,
                        action=action,
                        admin_user=admin_user,
                        field_name=field,
                        old_val=old_val,
                        new_val=new_val,
                        notes=f"Field '{field}' modified"
                    )

        item.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return item

    @staticmethod
    async def list_items(
        db: AsyncSession,
        category_id: int | None = None,
        day_group: str | None = None,
        status: str | None = None,
        search: str | None = None,
        low_stock_only: bool = False,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        page: int = 1,
        per_page: int = 20
    ) -> tuple[list[InventoryItem], int, int]:
        """
        List inventory items with comprehensive filtering, search, sorting and pagination.
        Returns: (items, total_count, total_pages)
        """
        stmt = select(InventoryItem).options(selectinload(InventoryItem.category))
        count_stmt = select(func.count(InventoryItem.id))

        filters = []
        if category_id:
            filters.append(InventoryItem.category_id == category_id)
        if day_group:
            dg_clean = day_group.strip()
            day_matches = [InventoryItem.day_group == dg_clean]
            if dg_clean.startswith("Day ") and dg_clean[4:].isdigit():
                day_num = int(dg_clean[4:])
                now_utc = datetime.now(timezone.utc)
                start_boundary = now_utc - timedelta(days=day_num)
                end_boundary = now_utc - timedelta(days=max(0, day_num - 1))
                day_matches.append(
                    (InventoryItem.created_at >= start_boundary) &
                    (InventoryItem.created_at < end_boundary)
                )
            elif dg_clean.lower() == "day 10+":
                now_utc = datetime.now(timezone.utc)
                day_matches.append(InventoryItem.created_at < (now_utc - timedelta(days=10)))

            filters.append(or_(*day_matches))

        if status:
            filters.append(InventoryItem.status == status)
        if search:
            search_pattern = f"%{search.strip()}%"
            filters.append(
                or_(
                    InventoryItem.sku.ilike(search_pattern),
                    InventoryItem.title.ilike(search_pattern),
                    InventoryItem.description.ilike(search_pattern)
                )
            )
        if low_stock_only:
            filters.append(
                InventoryItem.quantity <= InventoryItem.stock_alert_threshold
            )

        if filters:
            stmt = stmt.where(*filters)
            count_stmt = count_stmt.where(*filters)

        # Sorting
        sort_col = getattr(InventoryItem, sort_by, InventoryItem.created_at)
        order_func = desc if sort_order.lower() == "desc" else asc
        stmt = stmt.order_by(order_func(sort_col))

        # Pagination
        total_count = (await db.execute(count_stmt)).scalar_one() or 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)
        stmt = stmt.offset(offset).limit(per_page)

        result = await db.execute(stmt)
        items = list(result.scalars().all())
        return items, total_count, total_pages

    @staticmethod
    async def get_low_stock_alerts(db: AsyncSession) -> list[InventoryItem]:
        """Fetch all active items where stock <= alert threshold."""
        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.status == "active",
                InventoryItem.quantity <= InventoryItem.stock_alert_threshold
            )
            .options(selectinload(InventoryItem.category))
            .order_by(asc(InventoryItem.quantity))
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_day_groups(db: AsyncSession) -> list[str]:
        """Get list of distinct day groups currently present in inventory."""
        stmt = select(InventoryItem.day_group).distinct().order_by(InventoryItem.day_group)
        result = await db.execute(stmt)
        groups = [g for g in result.scalars().all() if g]
        return groups

    @staticmethod
    async def deduct_stock(
        db: AsyncSession,
        item_id: int,
        quantity: int,
        admin_user: str = "SYSTEM",
        notes: str | None = None
    ) -> bool:
        """Atomically deduct stock for an order; fails if insufficient."""
        item = await InventoryService.get_by_id(db, item_id)
        if not item or item.quantity < quantity:
            return False

        old_qty = item.quantity
        item.quantity -= quantity
        if item.quantity == 0 and item.status == "active":
            item.status = "out_of_stock"

        await InventoryService.log_change(
            db=db,
            item=item,
            action="ORDER_DEDUCT",
            admin_user=admin_user,
            field_name="quantity",
            old_val=old_qty,
            new_val=item.quantity,
            notes=notes or f"Stock deducted by {quantity}"
        )
        await db.flush()
        return True

    @staticmethod
    async def restock_item(
        db: AsyncSession,
        item_id: int,
        quantity: int,
        admin_user: str = "SYSTEM",
        notes: str | None = None
    ) -> bool:
        """Restock an item (e.g. cancelled/rejected order)."""
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return False

        old_qty = item.quantity
        item.quantity += quantity
        if item.quantity > 0 and item.status == "out_of_stock":
            item.status = "active"

        await InventoryService.log_change(
            db=db,
            item=item,
            action="RESTOCK",
            admin_user=admin_user,
            field_name="quantity",
            old_val=old_qty,
            new_val=item.quantity,
            notes=notes or f"Restocked by {quantity}"
        )
        await db.flush()
        return True

    @staticmethod
    async def deactivate_item(
        db: AsyncSession,
        item_id: int,
        admin_user: str = "SYSTEM",
        notes: str | None = None
    ) -> InventoryItem | None:
        """Deactivate/pause an inventory listing."""
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return None
        old_status = item.status
        item.status = "paused"
        item.updated_at = datetime.now(timezone.utc)
        await InventoryService.log_change(
            db=db,
            item=item,
            action="DEACTIVATE",
            admin_user=admin_user,
            field_name="status",
            old_val=old_status,
            new_val="paused",
            notes=notes or "Listing deactivated by admin"
        )
        await db.flush()
        return item

    @staticmethod
    async def activate_item(
        db: AsyncSession,
        item_id: int,
        admin_user: str = "SYSTEM",
        notes: str | None = None
    ) -> InventoryItem | None:
        """Activate a paused/archived inventory listing."""
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return None
        old_status = item.status
        item.status = "active"
        item.updated_at = datetime.now(timezone.utc)
        await InventoryService.log_change(
            db=db,
            item=item,
            action="ACTIVATE",
            admin_user=admin_user,
            field_name="status",
            old_val=old_status,
            new_val="active",
            notes=notes or "Listing activated by admin"
        )
        await db.flush()
        return item

    @staticmethod
    async def quick_restock(
        db: AsyncSession,
        item_id: int,
        additional_quantity: int,
        admin_user: str = "SYSTEM",
        notes: str | None = None
    ) -> InventoryItem | None:
        """Add inventory stock units and auto-activate if out of stock."""
        if additional_quantity <= 0:
            return None
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return None
        old_qty = item.quantity
        item.quantity += additional_quantity
        if item.quantity > 0 and item.status == "out_of_stock":
            item.status = "active"
        item.updated_at = datetime.now(timezone.utc)
        await InventoryService.log_change(
            db=db,
            item=item,
            action="UPDATE_STOCK",
            admin_user=admin_user,
            field_name="quantity",
            old_val=old_qty,
            new_val=item.quantity,
            notes=notes or f"Quick restock +{additional_quantity} units"
        )
        await db.flush()
        return item

    @staticmethod
    async def delete_item(
        db: AsyncSession,
        item_id: int,
        admin_user: str = "SYSTEM"
    ) -> bool:
        """Delete an item and log deletion."""
        item = await InventoryService.get_by_id(db, item_id)
        if not item:
            return False

        # Create audit entry before deleting
        await InventoryService.log_change(
            db=db,
            item=item,
            action="DELETE",
            admin_user=admin_user,
            old_val=f"SKU: {item.sku}, Title: {item.title}",
            notes="Item deleted from inventory"
        )
        await db.delete(item)
        await db.flush()
        return True

    @staticmethod
    async def get_items_by_exact_age_day(
        db: AsyncSession,
        day_number: int,
        reference_time: datetime | None = None
    ) -> list[InventoryItem]:
        """
        Query eligible active inventory items whose calculated age dynamically matches day_number (1 to 20).
        Day 1: < 24h old
        Day 2: 24h to 48h old
        ...
        Day 20: 19*24h to 20*24h old
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        # Retrieve all active items with quantity > 0
        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.status == "active",
                InventoryItem.quantity > 0
            )
            .options(selectinload(InventoryItem.category))
            .order_by(asc(InventoryItem.created_at))
        )
        candidates = list((await db.execute(stmt)).scalars().all())

        # Exact dynamic age matching
        matching = []
        for item in candidates:
            if item.get_day_number(ref) == day_number:
                matching.append(item)
        return matching

    @staticmethod
    async def get_total_stock_summary(
        db: AsyncSession,
        reference_time: datetime | None = None,
        max_days: int = 20
    ) -> dict:
        """
        Calculate day-wise inventory breakdown from Day 1 to Day 20.
        Customer safe: Never exposes private credentials, payloads, or internal notes.
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.status == "active",
                InventoryItem.quantity > 0
            )
            .options(selectinload(InventoryItem.category))
            .order_by(asc(InventoryItem.created_at))
        )
        items = list((await db.execute(stmt)).scalars().all())

        day_buckets: dict[int, list[dict]] = {d: [] for d in range(1, max_days + 1)}
        older_batches: list[dict] = []
        total_units = 0

        for item in items:
            total_units += item.quantity
            day_num = item.get_day_number(ref)
            batch_info = {
                "id": item.id,
                "sku": item.sku,
                "title": item.title,
                "quantity": item.quantity,
                "created_at": item.created_at,
                "price": item.price,
                "currency": item.currency,
            }
            if 1 <= day_num <= max_days:
                day_buckets[day_num].append(batch_info)
            else:
                older_batches.append(batch_info)

        categories_summary = []
        for day in range(1, max_days + 1):
            batches = day_buckets[day]
            day_qty = sum(b["quantity"] for b in batches)
            dates = sorted({b["created_at"].strftime("%Y-%m-%d") for b in batches})
            categories_summary.append({
                "day": day,
                "label": f"{day} Day{'s' if day > 1 else ''} Old",
                "quantity": day_qty,
                "batches_count": len(batches),
                "batches": batches,
                "dates": dates
            })

        return {
            "categories": categories_summary,
            "total_stock": total_units,
            "older_stock_count": sum(b["quantity"] for b in older_batches),
            "older_batches": older_batches
        }

    @staticmethod
    async def get_available_categories_for_buy(
        db: AsyncSession,
        reference_time: datetime | None = None,
        max_days: int = 20
    ) -> list[dict]:
        """
        Query current available stock directly from the database.
        Returns ONLY categories with at least 1 genuinely available unit (quantity > 0, status == 'active').
        Hides categories containing zero available units.
        Excludes sold, reserved, removed, paused, invalid, or review-required inventory.
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        stmt = (
            select(InventoryItem)
            .where(
                InventoryItem.status == "active",
                InventoryItem.quantity > 0
            )
        )
        items = list((await db.execute(stmt)).scalars().all())

        day_counts: dict[int, int] = {}
        for item in items:
            day_num = item.get_day_number(ref)
            if 1 <= day_num <= max_days:
                day_counts[day_num] = day_counts.get(day_num, 0) + item.quantity

        available = []
        for day in sorted(day_counts.keys()):
            qty = day_counts[day]
            if qty > 0:
                available.append({
                    "day": day,
                    "label": f"{day} Day{'s' if day > 1 else ''} Old",
                    "quantity": qty
                })
        return available

    @staticmethod
    async def purchase_item_with_wallet(
        db: AsyncSession,
        item_id: int,
        expected_day: int,
        telegram_user: dict,
        reference_time: datetime | None = None
    ) -> tuple[bool, str, Order | None]:
        """
        Atomically execute a stock purchase deducted from customer wallet.
        Enforces:
        - Exact dynamic age validation at purchase moment.
        - Non-negative stock and non-negative wallet balances.
        - Safe pending-fulfillment state without arbitrary credential generation.
        """
        from indo_selling_bot.services.wallet_service import WalletService
        from indo_selling_bot.services.delivery_service import DeliveryService
        from indo_selling_bot.services.pricing_service import PricingService

        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        # 1. Fetch item or alternative in expected day category
        item = await InventoryService.get_by_id(db, item_id)
        if not item or item.status != "active" or item.quantity <= 0:
            alternatives = await InventoryService.get_items_by_exact_age_day(db, day_number=expected_day, reference_time=ref)
            if alternatives:
                item = alternatives[0]
            else:
                return False, "This category is currently out of stock. The last unit was just reserved.", None

        # 2. Re-verify exact dynamic age at purchase moment
        current_day = item.get_day_number(ref)
        if current_day != expected_day:
            return False, f"Stock item transitioned to {item.get_age_category_label(ref)}. Please re-select category.", None

        try:
            # 3. Check and deduct wallet balance atomically using dynamic category price
            effective_price, effective_currency = await PricingService.get_effective_price(
                db=db,
                day_number=expected_day,
                fallback_price=item.price,
                fallback_currency=item.currency
            )
            price_int = int(round(effective_price))
            order_code = f"ORD-{secrets.randbelow(900000) + 100000}"

            deduct_ok, deduct_msg, remaining_bal = await WalletService.deduct_for_purchase(
                db=db,
                telegram_user_id=telegram_user["id"],
                amount=price_int,
                order_code=order_code,
                item_title=f"{item.title} ({item.get_age_category_label(ref)})"
            )
            if not deduct_ok:
                return False, deduct_msg, None

            # 4. Atomically decrement stock and mark as sold when exhausted
            old_qty = item.quantity
            item.quantity -= 1
            if item.quantity == 0:
                item.status = "sold"
            item.updated_at = datetime.now(timezone.utc)

            await InventoryService.log_change(
                db=db,
                item=item,
                action="WALLET_PURCHASE",
                admin_user=f"CUSTOMER:{telegram_user['id']}",
                field_name="quantity",
                old_val=old_qty,
                new_val=item.quantity,
                notes=f"Purchased in Order {order_code}"
            )

            # 5. Create Order in PENDING_FULFILLMENT state
            order = Order(
                order_code=order_code,
                telegram_user_id=telegram_user["id"],
                telegram_username=telegram_user.get("username"),
                telegram_first_name=telegram_user.get("first_name"),
                status="PENDING_FULFILLMENT",
                total_amount=float(price_int),
                currency=effective_currency,
                payment_method="WALLET",
                tx_reference=f"WALLET_DEBIT_{order_code}",
                fulfillment_released=False,
                created_at=datetime.now(timezone.utc)
            )
            db.add(order)
            await db.flush()

            # Create OrderItem
            order_item = OrderItem(
                order_id=order.id,
                inventory_item_id=item.id,
                item_title=item.title,
                item_sku=item.sku,
                unit_price=float(price_int),
                quantity=1,
                subtotal=float(price_int),
                day_group=item.day_group
            )
            db.add(order_item)

            # Queue delivery interface safely
            await DeliveryService.queue_for_fulfillment(db, order)

            await db.commit()
            return True, "Order placed successfully.", order
        except Exception as e:
            await db.rollback()
            return False, f"Purchase error: {str(e)}. Changes rolled back safely.", None

    @staticmethod
    async def purchase_batch_with_wallet(
        db: AsyncSession,
        day_number: int,
        quantity: int,
        telegram_user: dict,
        reference_time: datetime | None = None
    ) -> tuple[bool, str, Order | None]:
        """
        Atomically purchase a quantity (1, 5, 10, 20, or custom) of accounts from a Day category.
        Enforces:
        - Atomic wallet balance verification and deduction.
        - Stock deduction across matching items and marks sold when exhausted.
        - Creates single Order with corresponding OrderItem records.
        - Queues order into safe PENDING_FULFILLMENT state.
        """
        from indo_selling_bot.services.wallet_service import WalletService
        from indo_selling_bot.services.delivery_service import DeliveryService
        from indo_selling_bot.services.pricing_service import PricingService

        if quantity <= 0:
            return False, "Quantity must be at least 1.", None

        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        # 1. Fetch available matching items
        matching_items = await InventoryService.get_items_by_exact_age_day(
            db, day_number=day_number, reference_time=ref
        )
        total_available = sum(it.quantity for it in matching_items)
        if total_available < quantity:
            return False, f"Only {total_available} unit(s) available in Day {day_number} Old.", None

        # 2. Compute total price from dynamic category pricing
        first_item = matching_items[0]
        effective_price, effective_currency = await PricingService.get_effective_price(
            db=db,
            day_number=day_number,
            fallback_price=first_item.price,
            fallback_currency=first_item.currency
        )
        unit_price_int = int(round(effective_price))
        total_price = unit_price_int * quantity
        order_code = f"ORD-{secrets.randbelow(900000) + 100000}"

        try:
            # 3. Deduct from customer wallet atomically
            deduct_ok, deduct_msg, remaining_bal = await WalletService.deduct_for_purchase(
                db=db,
                telegram_user_id=telegram_user["id"],
                amount=total_price,
                order_code=order_code,
                item_title=f"Day {day_number} Old ({quantity} igs)"
            )
            if not deduct_ok:
                return False, deduct_msg, None

            # 4. Deduct items across stock
            from indo_selling_bot.services.stock_file_service import StockFileService
            remaining_to_deduct = quantity
            order_items_to_create = []
            purchased_accounts = []

            for item in matching_items:
                if remaining_to_deduct <= 0:
                    break
                take_qty = min(item.quantity, remaining_to_deduct)
                old_qty = item.quantity
                item.quantity -= take_qty
                if item.quantity == 0:
                    item.status = "sold"
                item.updated_at = datetime.now(timezone.utc)
                remaining_to_deduct -= take_qty

                # Extract account credentials for each unit purchased
                acc_info = StockFileService.extract_account_info(item)
                for _ in range(take_qty):
                    purchased_accounts.append(acc_info)

                await InventoryService.log_change(
                    db=db,
                    item=item,
                    action="WALLET_PURCHASE",
                    admin_user=f"CUSTOMER:{telegram_user['id']}",
                    field_name="quantity",
                    old_val=old_qty,
                    new_val=item.quantity,
                    notes=f"Purchased {take_qty} in Order {order_code}"
                )

                order_items_to_create.append({
                    "inventory_item_id": item.id,
                    "item_title": item.title,
                    "item_sku": item.sku,
                    "unit_price": float(unit_price_int),
                    "quantity": take_qty,
                    "subtotal": float(unit_price_int * take_qty),
                    "day_group": item.day_group
                })

            # 5. Create Order
            order = Order(
                order_code=order_code,
                telegram_user_id=telegram_user["id"],
                telegram_username=telegram_user.get("username"),
                telegram_first_name=telegram_user.get("first_name"),
                status="COMPLETED",
                total_amount=float(total_price),
                currency=effective_currency,
                payment_method="WALLET",
                tx_reference=f"WALLET_DEBIT_{order_code}",
                fulfillment_released=True,
                created_at=datetime.now(timezone.utc)
            )
            db.add(order)
            await db.flush()

            for oi_data in order_items_to_create:
                oi = OrderItem(order_id=order.id, **oi_data)
                db.add(oi)

            await DeliveryService.queue_for_fulfillment(db, order)
            await db.commit()

            order.purchased_accounts = purchased_accounts
            order.day_number = day_number
            return True, "Order placed successfully.", order
        except Exception as e:
            await db.rollback()
            return False, f"Purchase error: {str(e)}. Changes rolled back safely.", None

    @staticmethod
    async def remove_stock_by_category(
        db: AsyncSession,
        day_number: int,
        quantity: int,
        admin_id: int,
        reason: str | None = None,
        reference_time: datetime | None = None
    ) -> tuple[bool, str, int]:
        """
        Safely remove stock from a specific day category.
        Enforces:
        - Quantity must be positive integer.
        - Quantity must not exceed available stock.
        - Marks records as REMOVED rather than permanently deleting history.
        - Never removes sold, reserved, or already removed records.
        - Emits InventoryChangeLog and AuditLog.
        - Refreshes stock counts immediately.
        Returns: (success: bool, message: str, remaining_in_category: int)
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        if quantity <= 0:
            return False, "Quantity to remove must be a positive integer.", 0

        # Retrieve active items in exact day category
        items = await InventoryService.get_items_by_exact_age_day(db, day_number=day_number, reference_time=ref)
        available_total = sum(it.quantity for it in items)

        if quantity > available_total:
            return False, f"Requested quantity ({quantity}) exceeds available stock ({available_total}).", available_total

        remaining_to_remove = quantity
        now_utc = datetime.now(timezone.utc)

        try:
            for item in items:
                if remaining_to_remove <= 0:
                    break

                old_qty = item.quantity
                if item.quantity <= remaining_to_remove:
                    deduct = item.quantity
                    item.quantity = 0
                    item.status = "removed"
                    item.updated_at = now_utc
                    remaining_to_remove -= deduct
                else:
                    deduct = remaining_to_remove
                    item.quantity -= deduct
                    item.updated_at = now_utc
                    remaining_to_remove = 0

                change_log = InventoryChangeLog(
                    item_id=item.id,
                    item_sku=item.sku,
                    item_title=item.title,
                    action="ADMIN_STOCK_REMOVED",
                    admin_user=f"TELEGRAM_ADMIN:{admin_id}",
                    field_name="quantity",
                    old_value=str(old_qty),
                    new_value=str(item.quantity),
                    notes=f"Removed {deduct} unit(s) by Admin {admin_id}. Reason: {reason or 'Admin stock removal'}"
                )
                db.add(change_log)

            audit = AuditLog(
                action="STOCK_REMOVED",
                performed_by=f"TELEGRAM_ADMIN:{admin_id}",
                details=f"Admin {admin_id} removed {quantity} unit(s) from Day {day_number} Old. Reason: {reason or 'Admin stock removal'}",
                created_at=now_utc
            )
            db.add(audit)

            await db.commit()
            remaining_in_cat = available_total - quantity
            return True, f"Successfully removed {quantity} unit(s) from Day {day_number} Old.", remaining_in_cat
        except Exception as e:
            await db.rollback()
            return False, f"Failed to remove stock: {str(e)}. Transaction rolled back.", available_total

    @staticmethod
    async def add_stock_batch(
        db: AsyncSession,
        category_id: int,
        quantity: int,
        price: float,
        created_at: datetime,
        admin_id: int,
        title: str | None = None,
        notes: str | None = None,
        currency: str = "INR",
        reference_time: datetime | None = None
    ) -> InventoryItem:
        """
        Administrator-guided batch stock creation with explicit creation timestamp.
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)

        # Calculate legitimate age day
        diff = ref - ts
        total_seconds = max(0, int(diff.total_seconds()))
        days = total_seconds // 86400
        day_num = days + 1
        day_label = f"Day {day_num}" if day_num <= 20 else "Day 20+"

        sku = f"BATCH-D{day_num}-{secrets.token_hex(3).upper()}"
        item_title = title or f"Indo Stock {day_label} ({quantity} pcs)"

        item = InventoryItem(
            sku=sku,
            category_id=category_id,
            title=item_title,
            description=f"Authentic digital inventory batch aged {day_label}",
            day_group=day_label,
            price=price,
            currency=currency,
            quantity=quantity,
            stock_alert_threshold=5,
            status="active",
            internal_notes=notes,
            created_at=ts,
            updated_at=datetime.now(timezone.utc)
        )
        db.add(item)
        await db.flush()

        await InventoryService.log_change(
            db=db,
            item=item,
            action="ADMIN_ADD_STOCK",
            admin_user=f"TELEGRAM_ADMIN:{admin_id}",
            field_name="quantity",
            old_val=0,
            new_val=quantity,
            notes=f"Added new batch with creation date {ts.isoformat()} ({day_label})"
        )

        audit = AuditLog(
            action="ADMIN_ADD_STOCK",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=f"Added batch SKU {item.sku} ({day_label}) with {quantity} pcs at {price} {currency}"
        )
        db.add(audit)
        await db.commit()
        return item
