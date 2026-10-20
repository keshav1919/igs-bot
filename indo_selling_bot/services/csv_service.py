"""
CSV Import and Export service for inventory management, orders, and audit logs.
"""
import io
import csv
from typing import Any
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.order import Order
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.inventory_service import InventoryService

class CSVService:
    @staticmethod
    def get_inventory_sample_template() -> str:
        """Generate a downloadable sample CSV template with expected headers and demo rows."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "sku", "title", "category", "day_group", "price",
            "quantity", "currency", "stock_alert_threshold", "status",
            "fulfillment_type", "fulfillment_payload", "internal_notes", "description"
        ])
        writer.writerow([
            "LEGIT-KEY-D1", "Premium Digital License Key", "Software Licenses", "Day 1", "50000",
            "10", "IDR", "5", "active",
            "digital_asset", "LICENSE-ABCD-1234-XYZ", "Batch Day 1 official license stock", "High-speed authentic digital license"
        ])
        writer.writerow([
            "LEGIT-SUB-D5", "Monthly Cloud Hosting Credit", "Cloud Services", "Day 5", "125000",
            "25", "IDR", "5", "active",
            "digital_asset", "COUPON-CLOUD-50OFF-2026", "Cloud voucher batch", "Monthly hosting credit valid 30 days"
        ])
        return output.getvalue()

    @staticmethod
    async def export_inventory_csv(db: AsyncSession) -> str:
        """Export all inventory items to a CSV string."""
        stmt = select(InventoryItem).options(selectinload(InventoryItem.category)).order_by(InventoryItem.id)
        result = await db.execute(stmt)
        items = result.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "sku", "title", "category", "day_group", "price",
            "currency", "quantity", "stock_alert_threshold", "status",
            "fulfillment_type", "fulfillment_payload", "internal_notes", "description", "created_at"
        ])

        for it in items:
            cat_name = it.category.name if it.category else ""
            created_str = it.created_at.strftime("%Y-%m-%d %H:%M:%S") if it.created_at else ""
            writer.writerow([
                it.id,
                it.sku,
                it.title,
                cat_name,
                it.day_group,
                it.price,
                it.currency,
                it.quantity,
                it.stock_alert_threshold,
                it.status,
                it.fulfillment_type,
                it.fulfillment_payload or "",
                it.internal_notes or "",
                it.description or "",
                created_str
            ])

        return output.getvalue()

    @staticmethod
    async def import_inventory_csv(
        db: AsyncSession,
        csv_content: str,
        admin_user: str = "CSV_IMPORT"
    ) -> dict[str, Any]:
        """
        Import inventory items from CSV.
        Supports creating new categories on-the-fly and updating existing items by SKU.
        Returns detailed summary: created, updated, skipped, errors.
        """
        f = io.StringIO(csv_content.strip())
        reader = csv.DictReader(f)
        
        # Verify required headers
        required_headers = {"sku", "title", "price", "quantity"}
        if not reader.fieldnames or not required_headers.issubset(set(h.strip().lower() for h in reader.fieldnames if h)):
            return {
                "success": False,
                "error": f"Missing required CSV columns. Required: {', '.join(sorted(required_headers))}",
                "created": 0,
                "updated": 0,
                "errors": []
            }

        # Normalize field names mapping
        field_map = {orig: orig.strip().lower() for orig in (reader.fieldnames or [])}

        created_count = 0
        updated_count = 0
        errors = []

        # Cache categories
        cat_stmt = select(Category)
        all_cats = (await db.execute(cat_stmt)).scalars().all()
        categories_by_name = {c.name.lower(): c for c in all_cats}

        row_idx = 1
        for row in reader:
            row_idx += 1
            # Normalize row dict keys
            norm_row = {field_map.get(k, k): v.strip() if v else "" for k, v in row.items()}
            
            sku = norm_row.get("sku", "").upper()
            title = norm_row.get("title", "")
            price_str = norm_row.get("price", "0")
            qty_str = norm_row.get("quantity", "0")

            if not sku or not title:
                errors.append(f"Row {row_idx}: Missing SKU or Title.")
                continue

            try:
                price = float(price_str)
                quantity = int(qty_str)
            except ValueError:
                errors.append(f"Row {row_idx} ({sku}): Invalid numeric value for price or quantity.")
                continue

            # Resolve Category
            cat_name = norm_row.get("category", "General").strip() or "General"
            cat = categories_by_name.get(cat_name.lower())
            if not cat:
                slug = cat_name.lower().replace(" ", "-").replace("/", "-")
                cat = Category(
                    name=cat_name,
                    slug=f"{slug}-{int(datetime.now(timezone.utc).timestamp())}",
                    description=f"Auto-created from CSV import ({cat_name})",
                    is_active=True
                )
                db.add(cat)
                await db.flush()
                categories_by_name[cat_name.lower()] = cat

            day_group = norm_row.get("day_group", "Day 1") or "Day 1"
            currency = norm_row.get("currency", "IDR") or "IDR"
            threshold_str = norm_row.get("stock_alert_threshold", "5")
            threshold = int(threshold_str) if threshold_str.isdigit() else 5
            status = norm_row.get("status", "active") or "active"
            fulfillment_type = norm_row.get("fulfillment_type", "digital_asset") or "digital_asset"
            fulfillment_payload = norm_row.get("fulfillment_payload") or None
            internal_notes = norm_row.get("internal_notes") or None
            description = norm_row.get("description") or None

            # Check existing item
            existing = await InventoryService.get_by_sku(db, sku)
            if existing:
                # Update item
                updates = {
                    "title": title,
                    "price": price,
                    "quantity": quantity,
                    "category_id": cat.id,
                    "day_group": day_group,
                    "stock_alert_threshold": threshold,
                    "status": status,
                    "fulfillment_type": fulfillment_type,
                    "fulfillment_payload": fulfillment_payload,
                    "internal_notes": internal_notes,
                    "description": description
                }
                await InventoryService.update_item(db, existing, updates, admin_user=admin_user)
                updated_count += 1
            else:
                # Create item
                await InventoryService.create_item(
                    db=db,
                    sku=sku,
                    category_id=cat.id,
                    title=title,
                    price=price,
                    quantity=quantity,
                    day_group=day_group,
                    description=description,
                    currency=currency,
                    stock_alert_threshold=threshold,
                    status=status,
                    fulfillment_type=fulfillment_type,
                    fulfillment_payload=fulfillment_payload,
                    internal_notes=internal_notes,
                    admin_user=admin_user
                )
                created_count += 1

        return {
            "success": True,
            "created": created_count,
            "updated": updated_count,
            "errors": errors
        }

    @staticmethod
    async def export_orders_csv(db: AsyncSession) -> str:
        """Export all orders to a CSV string."""
        stmt = select(Order).options(selectinload(Order.items)).order_by(Order.id.desc())
        orders = (await db.execute(stmt)).scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "order_code", "telegram_user_id", "telegram_username", "status",
            "total_amount", "currency", "payment_method", "tx_reference",
            "verified_by_admin", "verified_at", "created_at"
        ])

        for o in orders:
            v_at = o.verified_at.strftime("%Y-%m-%d %H:%M:%S") if o.verified_at else ""
            c_at = o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else ""
            writer.writerow([
                o.order_code,
                o.telegram_user_id,
                o.telegram_username or "",
                o.status,
                o.total_amount,
                o.currency,
                o.payment_method,
                o.tx_reference or "",
                o.verified_by_admin or "",
                v_at,
                c_at
            ])

        return output.getvalue()

    @staticmethod
    async def export_audit_logs_csv(db: AsyncSession) -> str:
        """Export all audit log entries to a CSV string."""
        stmt = select(AuditLog).order_by(AuditLog.id.desc())
        logs = (await db.execute(stmt)).scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "action", "performed_by", "ip_address", "details", "created_at"])

        for l in logs:
            c_at = l.created_at.strftime("%Y-%m-%d %H:%M:%S") if l.created_at else ""
            writer.writerow([
                l.id,
                l.action,
                l.performed_by,
                l.ip_address or "",
                l.details or "",
                c_at
            ])

        return output.getvalue()
