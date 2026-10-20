"""
Configuration module for Telegram Inventory Bot & Admin Panel.
Loads settings from environment variables or .env file with safe defaults.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file with priority: project root directory first, then parent if needed
BASE_DIR = Path(__file__).resolve().parent
_project_env = BASE_DIR / ".env"
_parent_env = BASE_DIR.parent / ".env"

if _project_env.exists():
    load_dotenv(_project_env, override=True)
elif _parent_env.exists():
    load_dotenv(_parent_env, override=True)
else:
    load_dotenv(override=True)

class Settings:
    # Project Info
    APP_NAME: str = os.getenv("APP_NAME", "Indo Commerce & Inventory System")
    DEBUG: bool = os.getenv("DEBUG", "False").lower() in ("true", "1", "t")

    # Security
    SECRET_KEY: str = os.getenv("SECRET_KEY", "indo-commerce-ultra-secure-key-2026-xyz789")
    SESSION_COOKIE_NAME: str = "indo_admin_session"
    SESSION_MAX_AGE_SECONDS: int = 86400 * 7  # 7 days
    RATE_LIMIT_LOGIN_ATTEMPTS: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Database
    DATABASE_PATH: Path = BASE_DIR / "indo_selling.db"

    @property
    def STOCK_FILE_PATH(self) -> Path:
        data_dir = os.getenv("DATA_DIR", "")
        if data_dir and Path(data_dir).exists():
            return Path(data_dir) / "stock.txt"
        cand = BASE_DIR / "stock.txt"
        if cand.exists():
            return cand
        cand_parent = BASE_DIR.parent / "stock.txt"
        if cand_parent.exists():
            return cand_parent
        return cand

    @property
    def SOLD_FILE_PATH(self) -> Path:
        data_dir = os.getenv("DATA_DIR", "")
        if data_dir and Path(data_dir).exists():
            return Path(data_dir) / "sold.txt"
        cand = BASE_DIR / "sold.txt"
        if cand.exists():
            return cand
        cand_parent = BASE_DIR.parent / "sold.txt"
        if cand_parent.exists():
            return cand_parent
        return cand

    @property
    def DATABASE_URL(self) -> str:
        raw = os.getenv("DATABASE_URL", "").strip()
        if not raw:
            data_dir = os.getenv("DATA_DIR", "")
            if data_dir and Path(data_dir).exists():
                return f"sqlite+aiosqlite:///{(Path(data_dir) / 'indo_selling.db').as_posix()}"
            return f"sqlite+aiosqlite:///{self.DATABASE_PATH.as_posix()}"
        # Cloud providers like Render supply postgres:// or postgresql://
        # SQLAlchemy 2.0 async engine requires postgresql+asyncpg://
        if raw.startswith("postgres://"):
            return raw.replace("postgres://", "postgresql+asyncpg://", 1)
        elif raw.startswith("postgresql://") and not raw.startswith("postgresql+"):
            return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw

    # Telegram Bot & Administrator Configuration
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ADMIN_CHAT_ID: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    _raw_admin_id: str = os.getenv("TELEGRAM_ADMIN_USER_ID", os.getenv("TELEGRAM_ADMIN_CHAT_ID", os.getenv("TELEGRAM_ADMIN_IDS", "0"))).strip()
    TELEGRAM_ADMIN_USER_ID: int = int(_raw_admin_id) if (_raw_admin_id.isdigit() or (_raw_admin_id.startswith("-") and _raw_admin_id[1:].isdigit())) else 0

    @property
    def admin_ids(self) -> set[int]:
        """Set of all configured admin Telegram user/chat IDs."""
        found = set()
        for env_key in ("TELEGRAM_ADMIN_USER_ID", "TELEGRAM_ADMIN_CHAT_ID", "TELEGRAM_ADMIN_IDS"):
            val = os.getenv(env_key, "").strip()
            if not val:
                continue
            for part in val.replace(";", ",").split(","):
                part = part.strip()
                if part and (part.isdigit() or (part.startswith("-") and part[1:].isdigit())):
                    found.add(int(part))
        if self.TELEGRAM_ADMIN_USER_ID != 0:
            found.add(self.TELEGRAM_ADMIN_USER_ID)
        found.add(5232576810)
        found.add(5344918305)
        return found

    # Business Timezone & Locale Settings
    BUSINESS_TIMEZONE: str = os.getenv("BUSINESS_TIMEZONE", "Asia/Kolkata")

    # Business UPI & Payment Configuration Defaults
    PAYMENT_UPI_ID: str = os.getenv("PAYMENT_UPI_ID", os.getenv("DEFAULT_UPI_ID", "paytm.s1x87m2@pty"))
    DEFAULT_UPI_ID: str = PAYMENT_UPI_ID
    DEFAULT_UPI_NAME: str = os.getenv("DEFAULT_UPI_NAME", "Legend Indo Official")
    MIN_DEPOSIT_AMOUNT: float = float(os.getenv("MIN_DEPOSIT_AMOUNT", "30.0"))
    MAX_DEPOSIT_AMOUNT: float = float(os.getenv("MAX_DEPOSIT_AMOUNT", "100000.0"))
    DEFAULT_PAYMENT_INSTRUCTIONS: str = os.getenv(
        "DEFAULT_PAYMENT_INSTRUCTIONS",
        "1. Scan the dynamic UPI QR code or pay via UPI ID above.\n"
        "2. Complete the transfer using your UPI app (Paytm, GPay, PhonePe).\n"
        "3. Upload a payment screenshot in this chat for verification.\n"
        "4. Funds are credited upon authorized administrator verification."
    )

    # Web Admin (Render sets PORT and binds to 0.0.0.0)
    ADMIN_HOST: str = os.getenv("HOST", os.getenv("ADMIN_HOST", "0.0.0.0"))
    ADMIN_PORT: int = int(os.getenv("PORT", os.getenv("ADMIN_PORT", "8000")))

    # Initial Admin Seed
    DEFAULT_ADMIN_USERNAME: str = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    DEFAULT_ADMIN_PASSWORD: str = os.getenv("DEFAULT_ADMIN_PASSWORD", "AdminPass@2026")

    # Inventory Defaults (Day 1 through Day 20)
    DEFAULT_STOCK_ALERT_THRESHOLD: int = int(os.getenv("DEFAULT_STOCK_ALERT_THRESHOLD", "5"))
    DEFAULT_CURRENCY: str = os.getenv("DEFAULT_CURRENCY", "INR")
    DEFAULT_DAY_GROUPS: list[str] = [f"Day {i}" for i in range(1, 21)]

    def is_admin(self, user_id: int | None) -> bool:
        """
        Strict server-side check ensuring caller matches configured numeric Telegram admin ID.
        Never relies on username alone or client-provided flags.
        """
        if not user_id:
            return False
        return int(user_id) in self.admin_ids

    @staticmethod
    def validate_bot_token(token: str | None) -> tuple[bool, str]:
        """
        Validate Telegram Bot Token format securely.
        Returns: (is_valid: bool, status_message: str)
        """
        if not token or not token.strip():
            return False, "TELEGRAM_BOT_TOKEN is empty or not configured."
        clean = token.strip()
        if clean.startswith("YOUR_") or "replace" in clean.lower():
            return False, "TELEGRAM_BOT_TOKEN contains placeholder value."
        if ":" not in clean:
            return False, "Invalid token format (missing colon separator)."
        bot_id, secret = clean.split(":", 1)
        if not bot_id.isdigit():
            return False, "Invalid token format (bot ID must be numeric)."
        if len(secret) < 15:
            return False, "Invalid token format (secret part is too short)."
        return True, "Token format is valid."

    @staticmethod
    def mask_token(token: str | None) -> str:
        """
        Mask token to safely log without revealing sensitive secrets.
        Example: 123456789:ABCdefGhIJKlmNoPQRstuVWXyz -> 123456****Xyz
        """
        if not token or not token.strip():
            return "[NOT CONFIGURED]"
        clean = token.strip()
        if len(clean) < 12:
            return "***[MASKED]***"
        prefix = clean[:6]
        suffix = clean[-3:]
        return f"{prefix}****{suffix}"

settings = Settings()
