"""
Web administration routers package.
"""
from indo_selling_bot.web.routers.auth_router import auth_router
from indo_selling_bot.web.routers.dashboard import dashboard_router
from indo_selling_bot.web.routers.inventory_router import inventory_router
from indo_selling_bot.web.routers.category_router import category_router
from indo_selling_bot.web.routers.order_router import order_router
from indo_selling_bot.web.routers.support_router import support_router
from indo_selling_bot.web.routers.audit_router import audit_router
from indo_selling_bot.web.routers.settings_router import settings_router

__all__ = [
    "auth_router",
    "dashboard_router",
    "inventory_router",
    "category_router",
    "order_router",
    "support_router",
    "audit_router",
    "settings_router"
]
