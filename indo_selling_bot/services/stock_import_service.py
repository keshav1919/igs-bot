"""
Stock Import Service for TXT inventory file parsing, validation, previewing, and transactional persistence.

Supported formats:
1. ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS
   e.g. DEMO-0001|DAY_1_OLD|2026-10-18T12:00:00Z|AVAILABLE
2. ITEM_ID|CREATED_AT_UTC
   e.g. DEMO-0002|2026-10-17T15:30:00Z (Category derived automatically from timestamp age)
3. ITEM_ID|CREATED_AT_UTC|STATUS
4. ITEM_ID|CATEGORY|CREATED_AT_UTC

Enforces:
- UTF-8 and UTF-8 BOM encoding support
- Timestamp-based dynamic 24h ageing rules (Day 1 Old to 20 Days Old)
- In-file and database duplicate detection without silent overwrites
- Out-of-range (future or >20 days) items quarantined to 'Uncategorized / Review Required'
- Category mismatch detection (flags conflict and adheres to timestamp truth)
- Atomic transactional commits with automatic rollback on error
- Full audit and import history tracking
"""
import uuid
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.import_history import StockImportHistory
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.models.category import Category

logger = logging.getLogger("stock_import_service")

MAX_IMPORT_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB
MAX_IMPORT_LINES = 10000

@dataclass
class ParsedRecord:
    line_number: int
    raw_line: str
    item_id: str
    created_at: datetime  # UTC aware
    category_label: str  # e.g. "1 Day Old" or "Uncategorized / Review Required"
    day_number: int  # 1..20, or outside
    status: str  # "active", "review_required", "paused"
    is_valid: bool
    is_duplicate: bool
    needs_review: bool
    error_reason: str | None = None
    warning_reason: str | None = None
    provided_category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

@dataclass
class ImportPreview:
    session_id: str
    filename: str
    total_lines_read: int
    valid_records: list[ParsedRecord] = field(default_factory=list)
    duplicates: list[ParsedRecord] = field(default_factory=list)
    invalid_records: list[ParsedRecord] = field(default_factory=list)
    review_records: list[ParsedRecord] = field(default_factory=list)
    category_counts: dict[str, dict] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_valid(self) -> int:
        return len(self.valid_records)

    @property
    def total_duplicates(self) -> int:
        return len(self.duplicates)

    @property
    def total_invalid(self) -> int:
        return len(self.invalid_records)

    @property
    def total_review(self) -> int:
        return len(self.review_records)


class StockImportService:
    # In-memory session cache: session_id -> ImportPreview (TTL: 30 minutes)
    _sessions: dict[str, ImportPreview] = {}

    @classmethod
    def store_session(cls, preview: ImportPreview) -> str:
        cls._sessions[preview.session_id] = preview
        return preview.session_id

    @classmethod
    def get_session(cls, session_id: str) -> ImportPreview | None:
        return cls._sessions.get(session_id)

    @classmethod
    def remove_session(cls, session_id: str) -> None:
        cls._sessions.pop(session_id, None)

    @staticmethod
    def parse_timestamp(raw_str: str) -> datetime:
        """
        Parse ISO-8601, timezone-abbreviated, or standard verbal datetime formats into UTC-aware datetime.
        Supports 'IST' (+05:30), 'WIB' (+07:00), 'UTC', 'GMT', 'Z', and colons between date and time.
        """
        s = raw_str.strip()
        tz = timezone.utc

        # Handle timezone suffix
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

        # Try ISO format first
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            return dt.astimezone(timezone.utc)
        except Exception:
            pass

        # Try verbal and standard datetime formats
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
    def parse_line_tokens(cls, raw_line: str) -> tuple[str, str | None, str, str, dict] | str | None:
        """
        Parse raw line supporting multiple formats:
        1. Pipe-separated: ITEM_ID|CATEGORY|CREATED_AT_UTC|STATUS
        2. Pipe-separated compact: ITEM_ID|CREATED_AT_UTC
        3. Colon-separated account: ( mail:pass:username:date:time ) or mail:pass:username:date:time
        Returns:
            (item_id, category_or_none, timestamp_str, status_str, extra_dict) or 'HEADER' or 'INVALID' or None
        """
        clean = raw_line.strip().strip("()[]\"'").strip()
        if not clean or clean.startswith("#") or clean.startswith("//"):
            return None

        upper = clean.upper()
        if (
            upper.startswith("ITEM_ID")
            or upper.startswith("ITEM-ID")
            or upper.startswith("MAIL:PASS:USERNAME")
            or upper.startswith("EMAIL:PASS:USER")
        ):
            return "HEADER"

        # Format 1: Pipe-delimited
        if "|" in clean:
            tokens = [t.strip() for t in clean.split("|")]
            if len(tokens) == 2:
                return tokens[0][:64], None, tokens[1], "active", {}
            elif len(tokens) == 3:
                try:
                    cls.parse_timestamp(tokens[1])
                    return tokens[0][:64], None, tokens[1], tokens[2], {}
                except Exception:
                    return tokens[0][:64], tokens[1], tokens[2], "active", {}
            elif len(tokens) >= 4:
                return tokens[0][:64], tokens[1], tokens[2], tokens[3], {}

        # Format 2: Colon-delimited (mail:pass:username:cookie:date:time or mail:pass:username:date:time)
        if ":" in clean:
            parts = [p.strip() for p in clean.split(":")]
            # Search backwards for a valid timestamp segment
            for i in range(len(parts) - 1, 1, -1):
                cand_dt = ":".join(parts[i:]).strip()
                try:
                    cls.parse_timestamp(cand_dt)
                    mail = parts[0]
                    pwd = parts[1] if len(parts) > 1 else ""
                    middle = parts[2:i]
                    uname = ""
                    cookie = ""
                    if len(middle) >= 2:
                        uname = middle[0]
                        cookie = ":".join(middle[1:])
                    elif len(middle) == 1:
                        val = middle[0]
                        if "=" in val or ";" in val:
                            cookie = val
                        else:
                            uname = val
                    item_id = (uname if uname else mail)[:64]
                    extra = {"mail": mail, "password": pwd, "username": uname, "cookie": cookie, "raw_line": clean}
                    return item_id, None, cand_dt, "active", extra
                except Exception:
                    continue

            # Fallback if no timestamp segment matched: default to current time (Day 1)
            if len(parts) >= 2:
                mail = parts[0]
                pwd = parts[1]
                middle = parts[2:]
                uname = ""
                cookie = ""
                if len(middle) >= 2:
                    uname = middle[0]
                    cookie = ":".join(middle[1:])
                elif len(middle) == 1:
                    val = middle[0]
                    if "=" in val or ";" in val:
                        cookie = val
                    else:
                        uname = val
                item_id = (uname if uname else mail)[:64]
                now_str = datetime.now(timezone.utc).isoformat()
                extra = {"mail": mail, "password": pwd, "username": uname, "cookie": cookie, "raw_line": clean}
                return item_id, None, now_str, "active", extra

        return "INVALID"

    @staticmethod
    def calculate_age_day(
        created_at: datetime,
        reference_time: datetime | None = None
    ) -> tuple[int, str, bool, str | None]:
        """
        Calculate complete elapsed 24-hour periods from timestamp.
        Returns: (day_number, category_label, is_in_range, warning_or_error)
        """
        ref = reference_time or datetime.now(timezone.utc)
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        if created_at > ref:
            return 0, "Uncategorized / Review Required", False, "Future timestamp detected. Items cannot have negative age."

        elapsed_seconds = max(0, int((ref - created_at).total_seconds()))
        days = elapsed_seconds // 86400
        day_num = days + 1

        if 1 <= day_num <= 20:
            label = f"{day_num} Day{'s' if day_num > 1 else ''} Old"
            return day_num, label, True, None

        # Age > 20 days: Put into Uncategorized / Review Required
        label = "Uncategorized / Review Required"
        return day_num, label, False, f"Item age ({day_num} days) exceeds supported 1-20 days range."

    @staticmethod
    def normalize_category_string(cat_str: str) -> int | None:
        """
        Normalize input category label like 'DAY_1_OLD', 'DAY 1', 'Day 5 Old', 'DAY_20' to numeric day.
        Returns None if not recognized.
        """
        if not cat_str:
            return None
        s = cat_str.strip().upper().replace("_", " ")
        # Match patterns like 'DAY 1 OLD', 'DAY 1', '1 DAY OLD', '1 DAYS OLD', 'DAY1'
        m = re.search(r'\b(?:DAY\s*(\d{1,2})|(\d{1,2})\s*DAYS?\s*OLD)\b', s)
        if m:
            val = m.group(1) or m.group(2)
            try:
                num = int(val)
                if 1 <= num <= 20:
                    return num
            except ValueError:
                pass
        return None

    @classmethod
    async def parse_txt_file(
        cls,
        content_bytes: bytes,
        filename: str,
        db: AsyncSession,
        reference_time: datetime | None = None
    ) -> ImportPreview:
        """
        Parse raw bytes of uploaded TXT file, validate records, check for duplicates in file and DB,
        and assemble an ImportPreview.
        """
        session_id = f"imp_{uuid.uuid4().hex[:12]}"

        # Handle encoding: decode with utf-8-sig to automatically strip UTF-8 BOM
        try:
            text = content_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Fallback attempt with latin-1 or error
            try:
                text = content_bytes.decode("latin-1")
            except Exception as e:
                preview = ImportPreview(session_id=session_id, filename=filename, total_lines_read=0)
                preview.invalid_records.append(ParsedRecord(
                    line_number=0,
                    raw_line="",
                    item_id="N/A",
                    created_at=datetime.now(timezone.utc),
                    category_label="N/A",
                    day_number=0,
                    status="invalid",
                    is_valid=False,
                    is_duplicate=False,
                    needs_review=False,
                    error_reason=f"File encoding error: Could not decode as UTF-8 ({e})."
                ))
                cls.store_session(preview)
                return preview

        lines = text.splitlines()
        preview = ImportPreview(
            session_id=session_id,
            filename=filename,
            total_lines_read=len(lines)
        )

        if len(lines) > MAX_IMPORT_LINES:
            preview.invalid_records.append(ParsedRecord(
                line_number=0,
                raw_line="",
                item_id="LIMIT_EXCEEDED",
                created_at=datetime.now(timezone.utc),
                category_label="N/A",
                day_number=0,
                status="invalid",
                is_valid=False,
                is_duplicate=False,
                needs_review=False,
                error_reason=f"File exceeds maximum allowed line count of {MAX_IMPORT_LINES:,} lines (found {len(lines):,})."
            ))
            cls.store_session(preview)
            return preview

        # 1. First pass: extract candidate item IDs to perform a single batch DB query
        candidate_item_ids = set()
        parsed_candidates: list[dict] = []

        line_num = 0
        for raw in lines:
            line_num += 1
            res = cls.parse_line_tokens(raw)
            if res is None:
                continue
            if res == "HEADER":
                continue
            if res == "INVALID":
                preview.invalid_records.append(ParsedRecord(
                    line_number=line_num,
                    raw_line=raw,
                    item_id="UNKNOWN",
                    created_at=datetime.now(timezone.utc),
                    category_label="Invalid Syntax",
                    day_number=0,
                    status="invalid",
                    is_valid=False,
                    is_duplicate=False,
                    needs_review=False,
                    error_reason="Missing fields. Supported formats: mail:pass:username:date:time or ITEM_ID|CREATED_AT_UTC."
                ))
                continue

            item_id, provided_cat, created_at_raw, status_raw, extra = res
            candidate_item_ids.add(item_id)
            parsed_candidates.append({
                "line_num": line_num,
                "raw_line": raw,
                "item_id": item_id,
                "provided_cat": provided_cat,
                "created_at_raw": created_at_raw,
                "status_raw": status_raw,
                "extra": extra
            })

        # 2. Query DB once for existing SKUs to detect duplicates
        existing_db_skus = set()
        if candidate_item_ids:
            # Query in batches if candidate set is large
            cand_list = list(candidate_item_ids)
            for i in range(0, len(cand_list), 900):
                batch_ids = cand_list[i:i+900]
                stmt = select(InventoryItem.sku).where(InventoryItem.sku.in_(batch_ids))
                res = (await db.execute(stmt)).scalars().all()
                existing_db_skus.update(res)

        # 3. Second pass: validate each line and assign categories
        seen_in_file_ids: set[str] = set()

        for cand in parsed_candidates:
            line_num = cand["line_num"]
            raw = cand["raw_line"]
            item_id = cand["item_id"]
            provided_cat_raw = cand["provided_cat"]
            created_at_raw = cand["created_at_raw"]
            status_raw = cand["status_raw"]
            extra = cand["extra"]

            if not item_id:
                preview.invalid_records.append(ParsedRecord(
                    line_number=line_num,
                    raw_line=raw,
                    item_id="EMPTY",
                    created_at=datetime.now(timezone.utc),
                    category_label="Invalid",
                    day_number=0,
                    status="invalid",
                    is_valid=False,
                    is_duplicate=False,
                    needs_review=False,
                    error_reason="Item ID is empty."
                ))
                continue

            # Duplicate detection (in-file)
            if item_id in seen_in_file_ids:
                preview.duplicates.append(ParsedRecord(
                    line_number=line_num,
                    raw_line=raw,
                    item_id=item_id,
                    created_at=datetime.now(timezone.utc),
                    category_label="Duplicate",
                    day_number=0,
                    status="duplicate",
                    is_valid=False,
                    is_duplicate=True,
                    needs_review=False,
                    error_reason=f"Duplicate ITEM_ID '{item_id}' appeared earlier in this file."
                ))
                continue
            seen_in_file_ids.add(item_id)

            # Duplicate detection (in database)
            if item_id in existing_db_skus:
                preview.duplicates.append(ParsedRecord(
                    line_number=line_num,
                    raw_line=raw,
                    item_id=item_id,
                    created_at=datetime.now(timezone.utc),
                    category_label="Duplicate",
                    day_number=0,
                    status="duplicate",
                    is_valid=False,
                    is_duplicate=True,
                    needs_review=False,
                    error_reason=f"ITEM_ID '{item_id}' already exists in database inventory."
                ))
                continue

            # Parse timestamp with flexible formats
            try:
                created_at_dt = cls.parse_timestamp(created_at_raw)
            except Exception as e:
                preview.invalid_records.append(ParsedRecord(
                    line_number=line_num,
                    raw_line=raw,
                    item_id=item_id,
                    created_at=datetime.now(timezone.utc),
                    category_label="Invalid Date",
                    day_number=0,
                    status="invalid",
                    is_valid=False,
                    is_duplicate=False,
                    needs_review=False,
                    error_reason=f"Unrecognized or malformed timestamp '{created_at_raw}'. Must be ISO-8601 (e.g. 2026-10-01T12:00:00Z)."
                ))
                continue

            # Calculate age and derived category
            day_num, derived_cat, is_in_range, age_warning = StockImportService.calculate_age_day(
                created_at_dt, reference_time=reference_time
            )

            # Check category mismatch if explicit category was provided
            warning_msg = None
            if provided_cat_raw:
                expected_day = StockImportService.normalize_category_string(provided_cat_raw)
                if expected_day is not None and expected_day != day_num:
                    warning_msg = (
                        f"Category mismatch: File specified '{provided_cat_raw}', but timestamp resolves to '{derived_cat}'. "
                        "Following timestamp-based rule."
                    )
                elif expected_day is None and is_in_range:
                    warning_msg = f"Non-standard category label '{provided_cat_raw}' normalized to '{derived_cat}' based on timestamp."

            # Normalize status
            norm_status = status_raw.strip().lower()
            if norm_status in ("available", "active", "in_stock", "live"):
                final_status = "active"
            elif norm_status in ("paused", "hold"):
                final_status = "paused"
            elif norm_status in ("review", "review_required", "pending_review"):
                final_status = "review_required"
            else:
                final_status = "active"

            record = ParsedRecord(
                line_number=line_num,
                raw_line=raw,
                item_id=item_id,
                created_at=created_at_dt,
                category_label=derived_cat,
                day_number=day_num,
                status=final_status,
                is_valid=is_in_range,
                is_duplicate=False,
                needs_review=(not is_in_range),
                error_reason=age_warning if not is_in_range else None,
                warning_reason=warning_msg,
                provided_category=provided_cat_raw,
                extra=extra
            )

            if not is_in_range:
                preview.review_records.append(record)
            else:
                preview.valid_records.append(record)

        # 4. Aggregate category counts for valid records
        cat_counts: dict[str, dict] = {}
        for rec in preview.valid_records:
            cat = rec.category_label
            date_str = rec.created_at.strftime("%Y-%m-%d")
            if cat not in cat_counts:
                cat_counts[cat] = {
                    "day_number": rec.day_number,
                    "count": 0,
                    "dates": set()
                }
            cat_counts[cat]["count"] += 1
            cat_counts[cat]["dates"].add(date_str)

        # Format dates set to sorted list
        for cat, data in cat_counts.items():
            data["dates"] = sorted(list(data["dates"]))

        # Sort category counts by day_number
        preview.category_counts = dict(
            sorted(cat_counts.items(), key=lambda item: item[1]["day_number"])
        )

        cls.store_session(preview)
        return preview

    @classmethod
    async def commit_import(
        cls,
        session_id: str,
        admin_id: int,
        db: AsyncSession,
        default_price: float = 25000.0,
        currency: str = "INR",
        default_category_id: int | None = None
    ) -> tuple[bool, str, int]:
        """
        Persist validated records from an import session atomically to the database.
        Emits InventoryChangeLog, StockImportHistory, and AuditLog.
        On any exception, rolls back transaction cleanly.
        """
        preview = cls.get_session(session_id)
        if not preview:
            return False, "Import session expired or not found. Please upload the file again.", 0

        if not preview.valid_records:
            return False, "No valid records available to import in this session.", 0

        # Resolve category ID
        if default_category_id is None:
            cat_stmt = select(Category.id).order_by(Category.id).limit(1)
            default_category_id = (await db.execute(cat_stmt)).scalar_one_or_none() or 1

        now_utc = datetime.now(timezone.utc)

        try:
            # Atomic transactional write
            for rec in preview.valid_records:
                mail = rec.extra.get("mail") if rec.extra else None
                username = rec.extra.get("username") if rec.extra else None
                if mail and username:
                    item_title = f"Indo Account - {username}"
                    item_desc = f"Indo Account ({mail}) aged {rec.category_label}"
                    internal_notes = f"Account: {username} ({mail}) imported from '{preview.filename}' by Admin {admin_id}"
                elif mail:
                    item_title = f"Indo Account - {mail}"
                    item_desc = f"Indo Account ({mail}) aged {rec.category_label}"
                    internal_notes = f"Account: {mail} imported from '{preview.filename}' by Admin {admin_id}"
                else:
                    item_title = f"Indo Stock {rec.category_label} ({rec.item_id})"
                    item_desc = f"Batch stock imported from '{preview.filename}' aged {rec.category_label}"
                    internal_notes = f"Imported via TXT file '{preview.filename}' by Admin {admin_id}"

                item = InventoryItem(
                    sku=rec.item_id,
                    category_id=default_category_id,
                    title=item_title,
                    description=item_desc,
                    day_group=f"Day {rec.day_number}",
                    price=default_price,
                    currency=currency,
                    quantity=1,
                    stock_alert_threshold=5,
                    status=rec.status,
                    fulfillment_payload=rec.raw_line,
                    internal_notes=internal_notes,
                    created_at=rec.created_at,
                    updated_at=now_utc
                )
                db.add(item)
                await db.flush()

                # Add per-item change log
                change_log = InventoryChangeLog(
                    item_id=item.id,
                    item_sku=item.sku,
                    item_title=item.title,
                    action="TXT_IMPORT",
                    admin_user=f"TELEGRAM_ADMIN:{admin_id}",
                    field_name="quantity",
                    old_value=None,
                    new_value="1",
                    notes=f"Imported from {preview.filename} with creation date {rec.created_at.isoformat()}"
                )
                db.add(change_log)

            # Record persistent stock import history
            import_history = StockImportHistory(
                admin_id=admin_id,
                filename=preview.filename,
                total_lines=preview.total_lines_read,
                valid_count=preview.total_valid,
                duplicate_count=preview.total_duplicates,
                invalid_count=preview.total_invalid,
                review_count=preview.total_review,
                status="CONFIRMED",
                summary_notes=f"Imported {preview.total_valid} items into {len(preview.category_counts)} categories",
                created_at=now_utc
            )
            db.add(import_history)

            # Record system security audit log
            audit = AuditLog(
                action="TXT_STOCK_IMPORT",
                performed_by=f"TELEGRAM_ADMIN:{admin_id}",
                details=(
                    f"Imported '{preview.filename}': {preview.total_valid} valid saved, "
                    f"{preview.total_duplicates} duplicates skipped, "
                    f"{preview.total_invalid} invalid lines, "
                    f"{preview.total_review} review required"
                ),
                created_at=now_utc
            )
            db.add(audit)

            await db.commit()

            try:
                from indo_selling_bot.services.stock_txt_service import StockTxtService
                lines_to_add = "\n".join(rec.raw_line for rec in preview.valid_records)
                StockTxtService.add_stock_lines(lines_to_add)
            except Exception as fe:
                logger.error(f"Failed to append to stock.txt after import: {fe}")

            # Evict session from memory once committed
            cls.remove_session(session_id)
            return True, f"Successfully imported {preview.total_valid} valid inventory records.", preview.total_valid

        except Exception as e:
            await db.rollback()
            logger.error(f"Error committing stock import session {session_id}: {e}", exc_info=True)
            return False, f"Database transaction failed: {str(e)}. All changes rolled back.", 0

    @classmethod
    def cancel_import(cls, session_id: str, admin_id: int) -> bool:
        """Discard in-memory import session without modifying database."""
        if session_id in cls._sessions:
            cls.remove_session(session_id)
            return True
        return False
