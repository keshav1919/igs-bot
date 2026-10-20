"""
Entry point to run aiogram 3 Telegram Bot.
"""
import sys
from pathlib import Path

# Bootstrap sys.path to enable 'indo_selling_bot' package imports when run directly from project root
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT.parent))
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if "indo_selling_bot" not in sys.modules:
    try:
        import indo_selling_bot
    except ModuleNotFoundError:
        import types
        pkg = types.ModuleType("indo_selling_bot")
        pkg.__path__ = [str(_PROJECT_ROOT)]
        pkg.__file__ = str(_PROJECT_ROOT / "__init__.py")
        sys.modules["indo_selling_bot"] = pkg

import asyncio
import logging
from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db
from indo_selling_bot.bot.bot_instance import setup_bot_and_dispatcher

from aiogram.types import BotCommand
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("run_bot")

async def main():
    await init_db()

    bot, dp = setup_bot_and_dispatcher()
    if not bot:
        logger.info("[i] TELEGRAM_BOT_TOKEN is not configured or contains placeholder.")
        logger.info("[i] How to configure your Telegram Bot:")
        logger.info("    1. Open Telegram and search for @BotFather")
        logger.info("    2. Send /newbot and follow the prompts to name your bot")
        logger.info("    3. Copy the HTTP API token provided by BotFather")
        logger.info("    4. Paste it into your .env file: TELEGRAM_BOT_TOKEN=123456789:ABCdefGhIJKlmNoPQRstuVWXyz")
        logger.info("[i] Bot runner stopping cleanly. Web Admin panel continues running independently.")
        return

    # Register bot menu commands for left input button
    commands = [
        BotCommand(command="start", description="Start bot"),
        BotCommand(command="buy", description="Buy Indo accounts"),
        BotCommand(command="fund", description="Add funds to wallet"),
        BotCommand(command="owner", description="Owner details"),
    ]
    try:
        await bot.set_my_commands(commands)
        logger.info("[*] Successfully registered bot commands menu.")
    except Exception as cmd_err:
        logger.warning(f"[!] Warning registering bot commands menu: {cmd_err}")

    logger.info(f"[*] Starting aiogram 3 polling for bot [{settings.mask_token(settings.TELEGRAM_BOT_TOKEN)}]...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except TelegramNetworkError as net_err:
        logger.warning(f"[!] Telegram network connection error: {net_err}. Please check your internet connection.")
    except TelegramAPIError as api_err:
        logger.error(f"[!] Telegram API error encountered: {api_err}")
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("[*] Bot polling interrupted by user. Shutting down gracefully...")
    finally:
        await bot.session.close()
        logger.info("[*] Bot session closed cleanly.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[*] Bot process terminated.")
