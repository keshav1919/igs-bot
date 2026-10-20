"""
Payment and UPI settings service.
Manages business UPI ID, Payee Name, QR image file_id/path, and customer instructions.
"""
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.models.setting import StoreSetting
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.config import settings

class PaymentSettingService:
    SETTING_UPI_ID = "business_upi_id"
    SETTING_PAYEE_NAME = "business_payee_name"
    SETTING_QR_IMAGE = "business_qr_image"
    SETTING_INSTRUCTIONS = "payment_instructions"

    @staticmethod
    async def get_settings(db: AsyncSession) -> dict[str, str]:
        """Fetch active business payment configuration."""
        stmt = select(StoreSetting).where(
            StoreSetting.key.in_([
                PaymentSettingService.SETTING_UPI_ID,
                PaymentSettingService.SETTING_PAYEE_NAME,
                PaymentSettingService.SETTING_QR_IMAGE,
                PaymentSettingService.SETTING_INSTRUCTIONS
            ])
        )
        rows = list((await db.execute(stmt)).scalars().all())
        data = {r.key: r.value for r in rows}

        return {
            "upi_id": data.get(PaymentSettingService.SETTING_UPI_ID, settings.DEFAULT_UPI_ID),
            "payee_name": data.get(PaymentSettingService.SETTING_PAYEE_NAME, settings.DEFAULT_UPI_NAME),
            "qr_image": data.get(PaymentSettingService.SETTING_QR_IMAGE, ""),
            "instructions": data.get(PaymentSettingService.SETTING_INSTRUCTIONS, settings.DEFAULT_PAYMENT_INSTRUCTIONS),
        }

    @staticmethod
    async def update_setting(
        db: AsyncSession,
        key: str,
        value: str,
        admin_id: int,
        description: str | None = None
    ) -> None:
        """Update payment setting with audit logging."""
        stmt = select(StoreSetting).where(StoreSetting.key == key)
        setting = (await db.execute(stmt)).scalar_one_or_none()
        old_val = setting.value if setting else "[none]"
        if not setting:
            setting = StoreSetting(key=key, value=value, description=description)
            db.add(setting)
        else:
            setting.value = value
            setting.updated_at = datetime.now(timezone.utc)

        audit = AuditLog(
            action="UPDATE_PAYMENT_SETTING",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=f"Setting '{key}' changed from '{old_val}' to '{value}'"
        )
        db.add(audit)
        await db.commit()
