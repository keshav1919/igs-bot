"""
Telegram bot handlers package.
"""
from indo_selling_bot.bot.handlers.start import start_router
from indo_selling_bot.bot.handlers.catalog import catalog_router
from indo_selling_bot.bot.handlers.order import order_router
from indo_selling_bot.bot.handlers.my_orders import my_orders_router
from indo_selling_bot.bot.handlers.support import support_router
from indo_selling_bot.bot.handlers.buy_indo import buy_indo_router
from indo_selling_bot.bot.handlers.total_stock import total_stock_router
from indo_selling_bot.bot.handlers.add_fund import add_fund_router
from indo_selling_bot.bot.handlers.admin_stock import admin_stock_router

__all__ = [
    "start_router",
    "catalog_router",
    "order_router",
    "my_orders_router",
    "support_router",
    "buy_indo_router",
    "total_stock_router",
    "add_fund_router",
    "admin_stock_router",
]
