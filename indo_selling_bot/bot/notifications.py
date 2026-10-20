"""
Notification dispatcher for sending async Telegram notifications to customers.
Gracefully handles missing or unconfigured tokens during testing or offline runs.
"""
import logging
from aiogram import Bot
from indo_selling_bot.config import settings

logger = logging.getLogger("notifications")

async def send_customer_telegram_message(telegram_user_id: int, message_text: str) -> bool:
    """Send a direct Telegram message to a customer."""
    if not settings.TELEGRAM_BOT_TOKEN or settings.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
        logger.info(f"[SIMULATED TG NOTIFICATION to {telegram_user_id}]: {message_text[:80]}...")
        return True

    try:
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        await bot.send_message(
            chat_id=telegram_user_id,
            text=message_text,
            parse_mode="Markdown"
        )
        await bot.session.close()
        return True
    except Exception as e:
        logger.warning(f"Failed to send Telegram notification to {telegram_user_id}: {e}")
        return False
