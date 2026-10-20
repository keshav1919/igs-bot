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
from indo_selling_bot.config import settings
from indo_selling_bot.services.stock_txt_service import StockTxtService

logger = logging.getLogger(__name__)


class StockFileService:
    @classmethod
    def get_stock_path(cls) -> Path:
        return StockTxtService.get_stock_path()

    @classmethod
    def get_sold_path(cls) -> Path:
        return StockTxtService.get_sold_path()

    @classmethod
    def extract_account_info(cls, item_or_dict: Any) -> dict:
        """
        Extract email, password, username, domain, and original raw_line
        from dictionary or item object.
        """
        if isinstance(item_or_dict, dict):
            email = item_or_dict.get("email", "")
            password = item_or_dict.get("password", "654654@@")
            username = item_or_dict.get("username", "")
            domain = item_or_dict.get("domain") or (email.split("@")[-1] if "@" in email else "metamail.store")
            raw_line = item_or_dict.get("raw_line") or f"{email}:{password}:{username}"
            return {
                "email": email,
                "password": password,
                "username": username,
                "domain": domain,
                "raw_line": raw_line
            }

        # Fallback for SQL InventoryItem if passed
        payload = getattr(item_or_dict, "fulfillment_payload", "") or ""
        notes = getattr(item_or_dict, "internal_notes", "") or ""
        sku = getattr(item_or_dict, "sku", "") or ""

        if ":" in payload:
            parts = [p.strip() for p in payload.strip("()[]\"'").split(":")]
            email = parts[0]
            password = parts[1] if len(parts) > 1 else "654654@@"
            username = parts[2] if len(parts) > 2 else ""
            domain = email.split("@")[1] if "@" in email else "metamail.store"
            return {
                "email": email,
                "password": password,
                "username": username,
                "domain": domain,
                "raw_line": payload
            }

        import re
        mail_match = re.search(r'[\w\.-]+@[\w\.-]+', notes)
        email = mail_match.group(0) if mail_match else f"{sku.lower()}@metamail.store"
        domain = email.split("@")[1] if "@" in email else "metamail.store"
        password = payload if (payload and "@" not in payload and len(payload) < 30) else "654654@@"
        username = sku

        return {
            "email": email,
            "password": password,
            "username": username,
            "domain": domain,
            "raw_line": f"{email}:{password}:{username}"
        }

    @classmethod
    def record_sold_accounts(cls, accounts: list[dict]) -> None:
        """
        Append sold account lines to sold.txt using canonical dynamic path.
        """
        if not accounts:
            return

        lines_to_add = []
        for acc in accounts:
            raw = acc.get("raw_line") or f"{acc.get('email')}:{acc.get('password')}:{acc.get('username', '')}"
            lines_to_add.append(raw.strip())

        sold_path = cls.get_sold_path()
        content_to_append = "\n".join(lines_to_add) + "\n"
        try:
            with open(sold_path, "a", encoding="utf-8") as f:
                f.write(content_to_append)
            logger.info(f"Recorded {len(accounts)} sold accounts to {sold_path}")
        except Exception as e:
            logger.error(f"Failed to append to sold.txt: {e}")

    @classmethod
    async def sync_live_stock_txt(cls, db: Any = None) -> int:
        """
        Verify canonical stock.txt exists and return live count.
        """
        items = StockTxtService.read_all_stock()
        return len(items)

    @classmethod
    async def get_remaining_stock_txt_content(
        cls,
        db: Any = None,
        day_number: int | None = None
    ) -> tuple[str, int]:
        """
        Generate plain text content of remaining available accounts for sending to administrator.
        """
        return StockTxtService.get_remaining_stock_txt_content(day_number=day_number)
