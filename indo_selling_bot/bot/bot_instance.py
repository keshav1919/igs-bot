"""
aiogram 3 Bot and Dispatcher setup and router registration.
"""
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from indo_selling_bot.config import settings
from indo_selling_bot.bot.middlewares import ForceSubscriptionMiddleware
from indo_selling_bot.bot.handlers.start import start_router
from indo_selling_bot.bot.handlers.outlook import outlook_router
from indo_selling_bot.bot.handlers.buy_indo import buy_indo_router
from indo_selling_bot.bot.handlers.total_stock import total_stock_router
from indo_selling_bot.bot.handlers.add_fund import add_fund_router
from indo_selling_bot.bot.handlers.admin_stock import admin_stock_router
from indo_selling_bot.bot.handlers.admin_pricing import admin_pricing_router
from indo_selling_bot.bot.handlers.catalog import catalog_router
from indo_selling_bot.bot.handlers.order import order_router
from indo_selling_bot.bot.handlers.my_orders import my_orders_router
from indo_selling_bot.bot.handlers.support import support_router

logger = logging.getLogger("bot")

def setup_bot_and_dispatcher() -> tuple[Bot | None, Dispatcher]:
    """Create and configure the aiogram Bot and Dispatcher with all routers."""
    dp = Dispatcher(storage=MemoryStorage())

    # Register channel verification middleware
    dp.message.middleware(ForceSubscriptionMiddleware())
    dp.callback_query.middleware(ForceSubscriptionMiddleware())

    # Detach any existing parent bindings before including (prevents RuntimeError on restart)
    routers = [
        start_router,
        outlook_router,
        buy_indo_router,
        total_stock_router,
        add_fund_router,
        admin_stock_router,
        admin_pricing_router,
        catalog_router,
        order_router,
        my_orders_router,
        support_router,
    ]
    for r in routers:
        r._parent_router = None
        dp.include_router(r)

    bot = None
    is_valid, reason = settings.validate_bot_token(settings.TELEGRAM_BOT_TOKEN)
    if is_valid:
        bot = Bot(
            token=settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        logger.info(f"Bot initialized successfully with token [{settings.mask_token(settings.TELEGRAM_BOT_TOKEN)}].")
    else:
        logger.info(f"Bot token not active ({reason}). Polling will run in disabled/demo mode.")

    return bot, dp
