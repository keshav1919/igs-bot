"""
Payment instructions management and payment verification utilities.
Configurable payment channels with strict verification policy.
"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.models.setting import StoreSetting

DEFAULT_PAYMENT_CHANNELS = [
    {
        "id": "bank_bca",
        "name": "BCA (Bank Central Asia)",
        "account_name": "INDO COMMERCE OFFICIAL",
        "account_number": "8831092841",
        "type": "bank",
        "instructions": "Transfer exact amount. Use your Order Code as the transfer memo / reference."
    },
    {
        "id": "crypto_usdt_trc20",
        "name": "USDT (TRC-20 Network)",
        "account_name": "Official Tron Wallet",
        "account_number": "TX8q9PZ...TRC20OfficialStoreAddress",
        "type": "crypto",
        "instructions": "Send only USDT over TRC-20 network. Keep your transaction hash (TxID) ready to submit."
    },
    {
        "id": "qris_ewallet",
        "name": "QRIS / E-Wallet (GoPay, OVO, Dana)",
        "account_name": "INDO MERCHANT QR",
        "account_number": "NMID: ID1020092837110",
        "type": "qris",
        "instructions": "Scan QRIS or transfer to registered merchant. Input Order Code in description."
    }
]

class PaymentService:
    @staticmethod
    async def get_payment_methods(db: AsyncSession) -> list[dict]:
        """Retrieve configured payment methods from database or defaults."""
        stmt = select(StoreSetting).where(StoreSetting.key == "payment_methods")
        setting = (await db.execute(stmt)).scalar_one_or_none()
        if setting and setting.value:
            try:
                return json.loads(setting.value)
            except Exception:
                pass
        return DEFAULT_PAYMENT_CHANNELS

    @staticmethod
    async def save_payment_methods(db: AsyncSession, methods: list[dict]) -> None:
        """Update payment methods in database."""
        stmt = select(StoreSetting).where(StoreSetting.key == "payment_methods")
        setting = (await db.execute(stmt)).scalar_one_or_none()
        if not setting:
            setting = StoreSetting(
                key="payment_methods",
                value=json.dumps(methods, indent=2),
                description="Configured store payment instructions and accounts"
            )
            db.add(setting)
        else:
            setting.value = json.dumps(methods, indent=2)
        await db.flush()

    @staticmethod
    async def format_instructions_for_telegram(db: AsyncSession, order_code: str, total_amount: float, currency: str) -> str:
        """Format clean, markdown instructions for the customer on Telegram."""
        methods = await PaymentService.get_payment_methods(db)

        text = (
            f"💳 *PAYMENT INSTRUCTIONS FOR ORDER:* `{order_code}`\n"
            f"💰 *Total Due:* `{total_amount:,.2f} {currency}`\n\n"
            f"Please complete payment using one of our verified official channels below:\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        for m in methods:
            text += (
                f"🔹 *{m.get('name', 'Payment Channel')}*\n"
                f"• *Account / Wallet:* `{m.get('account_number', '-')}`\n"
                f"• *Name:* `{m.get('account_name', '-')}`\n"
                f"• *Guide:* {m.get('instructions', '')}\n\n"
            )

        text += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ *IMPORTANT SECURITY NOTICE:*\n"
            f"• Include your order code `{order_code}` in the transfer memo/notes.\n"
            f"• To confirm, click *'Submit Payment Reference'* and send your Transaction Hash, Bank Reference ID, or Sender Account Name.\n"
            f"• *Screenshots are not automatically verified.* Releases occur only upon authorized administrative verification.\n"
        )
        return text
