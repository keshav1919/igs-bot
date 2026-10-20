"""
Stock TXT Service.
Manages stock.txt and sold.txt as the authoritative source of truth for inventory.
Enforces:
- Thread-safe file access with threading.Lock
- Timestamp-based dynamic ageing (1 day old, 2 days old, etc.)
- Automatic fallback for lines without timestamp to 'now' (1 day old)
- Atomic deduction of sold accounts from stock.txt and appending to sold.txt
- Automatic file initialization for Windows and Render Linux environments
"""
import os
import re
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Any

from indo_selling_bot.config import settings

logger = logging.getLogger("stock_txt_service")


class StockTxtService:
    _lock = threading.Lock()
    _cached_mtime: float = -1.0
    _cached_items: list[dict] | None = None

    @classmethod
    def invalidate_cache(cls) -> None:
        """Invalidate the in-memory cache to force re-reading from disk."""
        cls._cached_mtime = -1.0
        cls._cached_items = None

    @classmethod
    def get_stock_path(cls) -> Path:
        """Get canonical path to stock.txt, creating it if it does not exist."""
        path = settings.STOCK_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
        except Exception as e:
            logger.error(f"Error initializing stock.txt at {path}: {e}")
        return path

    @classmethod
    def get_sold_path(cls) -> Path:
        """Get canonical path to sold.txt, creating it if it does not exist."""
        path = settings.SOLD_FILE_PATH
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.touch()
        except Exception as e:
            logger.error(f"Error initializing sold.txt at {path}: {e}")
        return path

    @classmethod
    def parse_timestamp(cls, raw_str: str) -> datetime:
        """
        Parse ISO-8601, timezone-abbreviated, or standard verbal datetime formats into UTC-aware datetime.
        Supports 'IST' (+05:30), 'WIB' (+07:00), 'UTC', 'GMT', 'Z', and colons between date and time.
        """
        s = raw_str.strip()
        tz = timezone.utc

        if s.endswith("IST"):
            tz = timezone(timedelta(hours=5, minutes=30))
            s = s[:-3].strip().rstrip(",")
        elif s.endswith("UTC") or s.endswith("GMT"):
            tz = timezone.utc
            s = s[:-3].strip().rstrip(",")
        elif s.endswith("WIB"):
            tz = timezone(timedelta(hours=7))
            s = s[:-3].strip().rstrip(",")
        elif s.endswith("Z"):
            tz = timezone.utc
            s = s[:-1].strip()

        # Normalize colon between date and time (e.g. '11 Oct 2026:01:14:43' -> '11 Oct 2026 01:14:43')
        s = re.sub(r'(\d{4}):(\d{1,2}:\d{2})', r'\1 \2', s)

        # Fast path 1: Try ISO format
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

        # Fast path 2: Try most common formats first (e.g. 11 Oct 2026, 01:14:43)
        for fmt in ("%d %b %Y, %H:%M:%S", "%d %b %Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                pass

        formats = [
            "%d %b %Y, %H:%M:%S",
            "%d %b %Y %H:%M:%S",
            "%d %b %Y, %I:%M:%S %p",
            "%d %b %Y %I:%M:%S %p",
            "%d %B %Y, %H:%M:%S",
            "%d %B %Y %H:%M:%S",
            "%d %b %Y, %H:%M",
            "%d %b %Y %H:%M",
            "%d %b %Y, %I:%M %p",
            "%d %b %Y %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d-%b-%Y, %H:%M:%S",
            "%d-%b-%Y %H:%M:%S",
            "%d-%b-%Y",
            "%d-%m-%Y",
            "%Y-%m-%d",
            "%d %b %Y",
            "%d %B %Y",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=tz).astimezone(timezone.utc)
            except ValueError:
                continue

        raise ValueError(f"Cannot parse datetime format: '{raw_str}'")

    @classmethod
    def calculate_day_number(cls, created_at: datetime, ref_time: datetime | None = None) -> int:
        """
        Calculate day age by calendar date difference in Asia/Kolkata (IST).
        Matches user's exact specification & old bot:
        - 18 Oct -> 2 days old (when today is 20 Oct)
        - 15 Oct -> 5 days old (22 units)
        - 14 Oct -> 6 days old (339 units)
        - 13 Oct -> 7 days old (869 units)
        - 12 Oct -> 8 days old (1002 units)
        - 11 Oct -> 9 days old (762 units)
        """
        tz = ZoneInfo("Asia/Kolkata")
        if ref_time:
            ref_dt = ref_time.astimezone(tz) if ref_time.tzinfo else ref_time.replace(tzinfo=timezone.utc).astimezone(tz)
        else:
            ref_dt = datetime.now(timezone.utc).astimezone(tz)

        item_dt = created_at.astimezone(tz) if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc).astimezone(tz)

        diff_days = (ref_dt.date() - item_dt.date()).days
        return max(1, diff_days)

    @classmethod
    def parse_line(cls, raw_line: str, ref_time: datetime | None = None) -> dict | None:
        """
        Parse raw line from stock.txt into structured account dict.
        Format: mail:pass:username:date:time or mail:pass:username or mail:pass
        If no timestamp is present, defaults to ref_time (or now), resolving to Day 1.
        Returns None for empty, comment, or invalid lines.
        """
        clean = raw_line.strip().strip("()[]\"'").strip()
        if not clean or clean.startswith("#") or clean.startswith("//"):
            return None

        upper = clean.upper()
        if upper.startswith("ITEM_ID") or upper.startswith("MAIL:PASS"):
            return None

        ref = ref_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)

        # 1. Pipe format support: ITEM_ID|CREATED_AT_UTC or mail:pass|date
        if "|" in clean and ":" not in clean.split("|")[0]:
            parts = [p.strip() for p in clean.split("|")]
            item_id = parts[0]
            ts_str = parts[1] if len(parts) > 1 else ""
            try:
                dt = cls.parse_timestamp(ts_str) if ts_str else ref
            except Exception:
                dt = ref
            day_num = cls.calculate_day_number(dt, ref)
            return {
                "email": item_id,
                "password": "",
                "username": item_id,
                "domain": "metamail.store",
                "created_at": dt,
                "day_number": day_num,
                "label": "1 day old" if day_num == 1 else f"{day_num} days old",
                "raw_line": clean
            }

        # 2. Colon-delimited format
        if ":" not in clean:
            return None

        parts = [p.strip() for p in clean.split(":")]
        if len(parts) < 2:
            return None

        email = parts[0]
        password = parts[1]
        username = ""
        created_at_dt = None

        # Search backwards for a valid timestamp segment
        if len(parts) >= 4:
            for i in range(2, len(parts)):
                cand_dt = ":".join(parts[i:]).strip()
                try:
                    created_at_dt = cls.parse_timestamp(cand_dt)
                    if i >= 3:
                        username = parts[i - 1]
                    break
                except Exception:
                    continue

        if created_at_dt is None and len(parts) >= 3:
            # Check if parts[2] is a timestamp or username
            try:
                created_at_dt = cls.parse_timestamp(parts[2])
            except Exception:
                username = parts[2]

        has_explicit_ts = (created_at_dt is not None)
        # Default timestamp if missing: default to 'now' (1 day old)
        if created_at_dt is None:
            created_at_dt = ref

        day_num = cls.calculate_day_number(created_at_dt, ref)
        domain = email.split("@")[-1] if "@" in email else "metamail.store"
        label = "1 day old" if day_num == 1 else f"{day_num} days old"

        return {
            "email": email,
            "password": password,
            "username": username,
            "domain": domain,
            "created_at": created_at_dt,
            "has_explicit_timestamp": has_explicit_ts,
            "day_number": day_num,
            "label": label,
            "raw_line": clean
        }

    @classmethod
    def read_all_stock(cls, ref_time: datetime | None = None) -> list[dict]:
        """Read and parse all stock entries from stock.txt with high-speed in-memory mtime cache."""
        stock_path = cls.get_stock_path()
        with cls._lock:
            if not stock_path.exists():
                return []

            try:
                mtime = os.path.getmtime(stock_path)
            except Exception:
                mtime = -1.0

            # If cache is valid and standard reference time is requested, return cached memory list instantly (<0.001ms)
            if ref_time is None and mtime > 0 and mtime == cls._cached_mtime and cls._cached_items is not None:
                return cls._cached_items

            try:
                with open(stock_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    raw_lines = f.readlines()
            except Exception as e:
                logger.error(f"Failed to read stock.txt from {stock_path}: {e}")
                return []

            parsed = []
            for line in raw_lines:
                item = cls.parse_line(line, ref_time=ref_time)
                if item:
                    parsed.append(item)

            if ref_time is None:
                cls._cached_mtime = mtime
                cls._cached_items = parsed

            return parsed

    @classmethod
    def get_available_categories(cls, ref_time: datetime | None = None, max_days: int = 20) -> list[dict]:
        """
        Query available age categories with genuine stock directly from stock.txt.
        Hides categories with 0 units.
        Returns sorted list: [{'day': 1, 'label': '1 day old', 'quantity': 5}, ...]
        """
        items = cls.read_all_stock(ref_time=ref_time)
        day_counts: dict[int, int] = {}
        for it in items:
            d = it["day_number"]
            day_counts[d] = day_counts.get(d, 0) + 1

        available = []
        for day in sorted(day_counts.keys()):
            qty = day_counts[day]
            if qty > 0:
                available.append({
                    "day": day,
                    "label": "1 day old" if day == 1 else f"{day} days old",
                    "quantity": qty
                })
        return available

    @classmethod
    def get_stock_for_day(cls, day_number: int, ref_time: datetime | None = None) -> list[dict]:
        """Fetch all available accounts matching an exact day age."""
        items = cls.read_all_stock(ref_time=ref_time)
        return [it for it in items if it["day_number"] == day_number]

    @classmethod
    def get_stock_count_for_day(cls, day_number: int, ref_time: datetime | None = None) -> int:
        """Count available accounts for a specific day category."""
        return len(cls.get_stock_for_day(day_number, ref_time=ref_time))

    @classmethod
    def get_total_stock_summary(cls, max_days: int = 20, ref_time: datetime | None = None) -> dict:
        """
        Generate day-wise inventory breakdown from stock.txt (Day 1 to Day 20).
        Safe: Never exposes credentials.
        """
        items = cls.read_all_stock(ref_time=ref_time)
        day_buckets: dict[int, list[dict]] = {d: [] for d in range(1, max_days + 1)}
        older_batches: list[dict] = []
        total_units = len(items)

        for item in items:
            day_num = item["day_number"]
            batch_info = {
                "id": item["email"],
                "sku": item["email"],
                "title": f"Indo - {item.get('username') or item['email']}",
                "quantity": 1,
                "created_at": item["created_at"],
                "price": 0.0,
                "currency": "INR",
            }
            if 1 <= day_num <= max_days:
                day_buckets[day_num].append(batch_info)
            else:
                older_batches.append(batch_info)

        categories_summary = []
        tz = ZoneInfo("Asia/Kolkata")
        for day in range(1, max_days + 1):
            batches = day_buckets[day]
            day_qty = len(batches)
            dates = sorted({(b["created_at"].astimezone(tz) if b["created_at"].tzinfo else b["created_at"]).strftime("%Y-%m-%d") for b in batches})
            categories_summary.append({
                "day": day,
                "label": "1 day old" if day == 1 else f"{day} days old",
                "quantity": day_qty,
                "batches_count": day_qty,
                "batches": batches,
                "dates": dates
            })

        return {
            "categories": categories_summary,
            "total_stock": total_units,
            "older_stock_count": len(older_batches),
            "older_batches": older_batches
        }

    @classmethod
    def purchase_accounts(
        cls,
        day_number: int,
        quantity: int,
        ref_time: datetime | None = None
    ) -> tuple[bool, str, list[dict]]:
        """
        Atomically purchase a quantity of accounts from stock.txt for a specific day.
        1. Reads stock.txt with thread lock.
        2. Filters accounts matching day_number.
        3. If sufficient, removes them from stock.txt and writes back.
        4. Appends purchased accounts to sold.txt.
        Returns: (success: bool, message: str, purchased_accounts: list[dict])
        """
        if quantity <= 0:
            return False, "Quantity must be at least 1.", []

        stock_path = cls.get_stock_path()
        sold_path = cls.get_sold_path()

        with cls._lock:
            if not stock_path.exists():
                return False, "Stock file is empty. No inventory available.", []

            try:
                with open(stock_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    raw_lines = f.readlines()
            except Exception as e:
                logger.error(f"Failed to read {stock_path}: {e}")
                return False, f"Could not read stock: {e}", []

            matching_indices = []
            parsed_items = []
            for idx, line in enumerate(raw_lines):
                parsed = cls.parse_line(line, ref_time=ref_time)
                parsed_items.append((idx, line, parsed))
                if parsed and parsed["day_number"] == day_number:
                    matching_indices.append(idx)

            if len(matching_indices) < quantity:
                return False, f"Only {len(matching_indices)} unit(s) available in Day {day_number} Old.", []

            selected_indices = set(matching_indices[:quantity])
            purchased_accounts = [
                parsed for idx, line, parsed in parsed_items
                if idx in selected_indices and parsed is not None
            ]

            remaining_lines = [
                line for idx, line, parsed in parsed_items
                if idx not in selected_indices
            ]
            sold_lines = [
                line.strip() for idx, line, parsed in parsed_items
                if idx in selected_indices
            ]

            # 1. Write remaining back to stock.txt
            try:
                with open(stock_path, "w", encoding="utf-8") as f:
                    f.writelines(remaining_lines)
                cls.invalidate_cache()
            except Exception as e:
                logger.error(f"Failed to write updated stock.txt: {e}")
                return False, f"Failed to update stock file: {e}", []

            # 2. Append sold lines to sold.txt
            try:
                sold_content = "\n".join(sold_lines) + "\n"
                with open(sold_path, "a", encoding="utf-8") as f:
                    f.write(sold_content)
            except Exception as e:
                logger.error(f"Failed to append to sold.txt: {e}")

        logger.info(f"Successfully sold {quantity} accounts for Day {day_number}. Remaining lines: {len(remaining_lines)}")
        return True, "Success", purchased_accounts

    @classmethod
    def add_stock_lines(cls, raw_text: str) -> tuple[int, int, list[str]]:
        """
        Parse raw text pasted by administrator, format, and append valid lines to stock.txt.
        If a line lacks a timestamp, current IST timestamp is automatically appended.
        Returns: (added_count, skipped_count, error_messages)
        """
        lines = raw_text.splitlines()
        added_count = 0
        skipped_count = 0
        errors = []
        lines_to_append = []

        now_ist = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Kolkata"))
        default_time_str = now_ist.strftime("%d %b %Y, %H:%M:%S IST")

        for line_idx, line in enumerate(lines, start=1):
            clean = line.strip().strip("()[]\"'").strip()
            if not clean or clean.startswith("#") or clean.startswith("//"):
                continue

            parsed = cls.parse_line(clean)
            if not parsed or not parsed.get("email") or not parsed.get("password"):
                errors.append(f"Line {line_idx}: Invalid format (must have email:pass)")
                skipped_count += 1
                continue

            if parsed.get("has_explicit_timestamp"):
                formatted_line = clean
            else:
                email = parsed["email"]
                pwd = parsed["password"]
                uname = parsed.get("username", "")
                formatted_line = f"{email}:{pwd}:{uname}:{default_time_str}"

            lines_to_append.append(formatted_line)
            added_count += 1

        if lines_to_append:
            stock_path = cls.get_stock_path()
            with cls._lock:
                try:
                    with open(stock_path, "a", encoding="utf-8") as f:
                        f.write("\n" + "\n".join(lines_to_append) + "\n")
                    cls.invalidate_cache()
                except Exception as e:
                    logger.error(f"Failed to append lines to stock.txt: {e}")
                    return 0, skipped_count, [f"Disk write error: {e}"]

        return added_count, skipped_count, errors

    @classmethod
    def remove_stock_by_day(
        cls,
        day_number: int,
        quantity: int,
        ref_time: datetime | None = None
    ) -> tuple[bool, str, int]:
        """
        Remove up to `quantity` lines matching day_number from stock.txt (admin removal).
        Returns: (success: bool, message: str, remaining_in_category: int)
        """
        if quantity <= 0:
            return False, "Quantity must be a positive integer.", 0

        stock_path = cls.get_stock_path()
        with cls._lock:
            if not stock_path.exists():
                return False, "Stock file is empty.", 0

            try:
                with open(stock_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    raw_lines = f.readlines()
            except Exception as e:
                return False, f"Could not read stock: {e}", 0

            matching_indices = []
            for idx, line in enumerate(raw_lines):
                parsed = cls.parse_line(line, ref_time=ref_time)
                if parsed and parsed["day_number"] == day_number:
                    matching_indices.append(idx)

            available = len(matching_indices)
            if quantity > available:
                return False, f"Requested quantity ({quantity}) exceeds available stock ({available}).", available

            remove_set = set(matching_indices[:quantity])
            remaining_lines = [line for idx, line in enumerate(raw_lines) if idx not in remove_set]

            try:
                with open(stock_path, "w", encoding="utf-8") as f:
                    f.writelines(remaining_lines)
                cls.invalidate_cache()
            except Exception as e:
                return False, f"Failed to save updated stock: {e}", available

        remaining = available - quantity
        return True, f"Successfully removed {quantity} unit(s) from Day {day_number} Old.", remaining

    @classmethod
    def get_remaining_stock_txt_content(
        cls,
        day_number: int | None = None,
        ref_time: datetime | None = None
    ) -> tuple[str, int]:
        """
        Generate plain text content of remaining available accounts for sending to administrator.
        If day_number is provided, filters for that specific day.
        """
        items = cls.read_all_stock(ref_time=ref_time)
        if day_number is not None:
            items = [it for it in items if it["day_number"] == day_number]

        lines = [it["raw_line"] for it in items]
        return "\n".join(lines), len(lines)
