"""
Stock File Service for maintaining live stock.txt and sold.txt records,
and generating remaining stock files for admin notifications upon purchase.
All files are maintained in the exact import format:
mail:pass:username:date:time (e.g. sahilkhanna4544@metamail.store:654654@@:sahil721219:11 Oct 2026, 01:13:35 IST)
"""
import logging
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.models.inventory import InventoryItem

logger = logging.getLogger(__name__)

# Primary and secondary project roots for dual-sync
PROJECT_DIRS = [
    Path(r"C:\indo selling bot project\indo_selling_bot"),
    Path(r"C:\Users\raipb\Downloads\Telegram Desktop\PARENT-AUTOMATION-v2\indo_selling_bot")
]


class StockFileService:
    @classmethod
    def get_formatted_stock_line(cls, item: InventoryItem) -> str:
        """
        Format an item in the exact format accepted by import:
        mail:pass:username:date:time (e.g. sahilkhanna4544@metamail.store:654654@@:sahil721219:11 Oct 2026, 01:13:35 IST)
        """
        raw = (item.fulfillment_payload or "").strip().strip("()\"'").strip()
        if ":" in raw:
            parts = raw.split(":")
            if len(parts) >= 4:
                return raw

        acc = cls.extract_account_info(item)
        email = acc.get("email") or f"{item.sku.lower()}@metamail.store"
        pwd = acc.get("password") or "654654@@"
        uname = acc.get("username") or ""

        created = item.created_at or datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        try:
            dt_ist = created.astimezone(ZoneInfo("Asia/Kolkata"))
            time_str = dt_ist.strftime("%d %b %Y, %H:%M:%S IST")
        except Exception:
            time_str = created.strftime("%d %b %Y, %H:%M:%S UTC")

        return f"{email}:{pwd}:{uname}:{time_str}"

    @staticmethod
    def extract_account_info(item: InventoryItem) -> dict:
        """
        Extract email, password, username, domain, and original raw_line
        from InventoryItem fulfillment_payload, internal_notes, or sku.
        """
        payload = (item.fulfillment_payload or "").strip()
        notes = (item.internal_notes or "").strip()
        sku = (item.sku or "").strip()

        # Format: mail:pass:username:date:time or mail:pass:username or mail:pass
        if ":" in payload:
            parts = [p.strip() for p in payload.strip("()[]\"'").split(":")]
            email = parts[0]
            password = parts[1] if len(parts) > 1 else "654654@@"
            username = ""

            if len(parts) >= 4:
                cand_user = parts[2]
                months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
                if not any(m in cand_user.lower() for m in months) and cand_user:
                    username = cand_user
            elif len(parts) == 3:
                cand_user = parts[2]
                months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
                if not any(m in cand_user.lower() for m in months) and cand_user:
                    username = cand_user

            domain = email.split("@")[1] if "@" in email else "metamail.store"
            return {
                "email": email,
                "password": password,
                "username": username,
                "domain": domain,
                "raw_line": payload
            }

        # Fallback from internal_notes if email was in notes e.g. "Account: testuser (email@metamail.store)"
        import re
        mail_match = re.search(r'[\w\.-]+@[\w\.-]+', notes)
        email = mail_match.group(0) if mail_match else f"{sku.lower()}@metamail.store"
        domain = email.split("@")[1] if "@" in email else "metamail.store"
        password = payload if (payload and "@" not in payload and len(payload) < 30) else "654654@@"
        username = sku if not (sku.startswith("DEMO") or sku.startswith("LIC") or sku.startswith("CLOUD") or sku.startswith("DIGI") or sku.startswith("ENT")) else ""

        created = item.created_at or datetime.now(timezone.utc)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        try:
            dt_ist = created.astimezone(ZoneInfo("Asia/Kolkata"))
            time_str = dt_ist.strftime("%d %b %Y, %H:%M:%S IST")
        except Exception:
            time_str = created.strftime("%d %b %Y, %H:%M:%S UTC")

        raw_line = f"{email}:{password}:{username}:{time_str}"

        return {
            "email": email,
            "password": password,
            "username": username,
            "domain": domain,
            "raw_line": raw_line
        }

    @classmethod
    def record_sold_accounts(cls, accounts: list[dict]) -> None:
        """
        Append sold account lines to sold.txt across both primary and secondary workspaces
        in exact import format.
        """
        if not accounts:
            return

        lines_to_add = []
        for acc in accounts:
            raw = acc.get("raw_line") or f"{acc['email']}:{acc['password']}:{acc.get('username', '')}"
            lines_to_add.append(raw.strip())

        content_to_append = "\n".join(lines_to_add) + "\n"
        for pdir in PROJECT_DIRS:
            try:
                sold_file = pdir / "sold.txt"
                with open(sold_file, "a", encoding="utf-8") as f:
                    f.write(content_to_append)
                logger.info(f"Recorded {len(accounts)} sold accounts to {sold_file}")
            except Exception as e:
                logger.error(f"Failed to append to sold.txt in {pdir}: {e}")

    @classmethod
    async def sync_live_stock_txt(cls, db: AsyncSession) -> int:
        """
        Rewrite stock.txt across both workspaces to match current genuinely available active inventory
        in exact import format.
        """
        stmt = (
            select(InventoryItem)
            .where(InventoryItem.status == "active", InventoryItem.quantity > 0)
            .order_by(InventoryItem.created_at.desc())
        )
        res = await db.execute(stmt)
        items = list(res.scalars().all())

        lines = []
        for it in items:
            line = cls.get_formatted_stock_line(it)
            for _ in range(it.quantity):
                lines.append(line)

        stock_content = "\n".join(lines) + ("\n" if lines else "")
        for pdir in PROJECT_DIRS:
            try:
                stock_file = pdir / "stock.txt"
                with open(stock_file, "w", encoding="utf-8") as f:
                    f.write(stock_content)
                logger.info(f"Synchronized {len(lines)} live accounts to {stock_file}")
            except Exception as e:
                logger.error(f"Failed to update stock.txt in {pdir}: {e}")

        return len(lines)

    @classmethod
    async def get_remaining_stock_txt_content(
        cls,
        db: AsyncSession,
        day_number: int | None = None
    ) -> tuple[str, int]:
        """
        Generate plain text content of remaining available accounts for sending to administrator
        in the exact same format that was imported.
        If day_number is specified, returns remaining units in that day category.
        """
        from indo_selling_bot.services.inventory_service import InventoryService

        if day_number:
            items = await InventoryService.get_items_by_exact_age_day(db, day_number=day_number)
        else:
            stmt = select(InventoryItem).where(InventoryItem.status == "active", InventoryItem.quantity > 0)
            res = await db.execute(stmt)
            items = list(res.scalars().all())

        lines = []
        for it in items:
            line = cls.get_formatted_stock_line(it)
            for _ in range(it.quantity):
                lines.append(line)

        return "\n".join(lines), len(lines)
