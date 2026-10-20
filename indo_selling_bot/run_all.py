"""
Dual runner script: Runs both FastAPI Web Admin Panel and aiogram 3 Bot concurrently.
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
import uvicorn
from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db
from indo_selling_bot.bot.bot_instance import setup_bot_and_dispatcher
from indo_selling_bot.web.app import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("indo_launcher")

async def run_web_server():
    config = uvicorn.Config(
        app=app,
        host=settings.ADMIN_HOST,
        port=settings.ADMIN_PORT,
        log_level="info"
    )
    server = uvicorn.Server(config)
    logger.info(f"[*] Admin Panel listening at http://{settings.ADMIN_HOST}:{settings.ADMIN_PORT}")
    await server.serve()

async def run_telegram_bot():
    bot, dp = setup_bot_and_dispatcher()
    if not bot:
        logger.warning("[!] Telegram Bot Token not set. Web Admin will run standalone; Bot polling disabled.")
        return

    logger.info(f"[*] Starting Telegram Bot polling [{settings.mask_token(settings.TELEGRAM_BOT_TOKEN)}]...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True
        )
    except Exception as e:
        logger.warning(f"[!] Telegram Bot polling stopped: {e}. Web Admin continues running.")
    finally:
        await bot.session.close()

async def main():
    await init_db()
    try:
        from indo_selling_bot.services.stock_txt_service import StockTxtService
        StockTxtService.get_stock_path()
        StockTxtService.get_sold_path()
    except Exception as e:
        logger.warning(f"[*] Stock txt files initialization: {e}")
    try:
        from indo_selling_bot.seed_data import seed_all
        await seed_all()
    except Exception as e:
        logger.warning(f"[*] Baseline database seeder note: {e}")

    await asyncio.gather(
        run_web_server(),
        run_telegram_bot()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("\n[*] Application shutdown complete.")
