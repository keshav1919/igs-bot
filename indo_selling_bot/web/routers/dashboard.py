"""
Dashboard analytics, sales metrics, and system overview router.
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from indo_selling_bot.database import get_db
from indo_selling_bot.models.order import Order, OrderStatus
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.models.support import SupportTicket
from indo_selling_bot.web.dependencies import get_current_admin

dashboard_router = APIRouter()

@dashboard_router.get("/", response_class=HTMLResponse)
@dashboard_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    """Render the dark responsive dashboard with live KPIs."""
    templates = request.app.state.templates

    # 1. Total Revenue from verified/fulfilled orders
    rev_stmt = select(func.sum(Order.total_amount)).where(
        Order.status.in_([OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED])
    )
    total_revenue = (await db.execute(rev_stmt)).scalar() or 0.0

    # 2. Total orders count
    orders_count_stmt = select(func.count(Order.id))
    total_orders = (await db.execute(orders_count_stmt)).scalar() or 0

    # 3. Verified orders count
    verified_stmt = select(func.count(Order.id)).where(
        Order.status.in_([OrderStatus.PAID_VERIFIED, OrderStatus.FULFILLED])
    )
    verified_orders_count = (await db.execute(verified_stmt)).scalar() or 0

    # 4. Pending payment verifications count (Submitted)
    pending_verif_stmt = select(func.count(Order.id)).where(
        Order.status == OrderStatus.PAYMENT_SUBMITTED
    )
    pending_verifications_count = (await db.execute(pending_verif_stmt)).scalar() or 0

    # 5. Low stock items
    low_stock_stmt = (
        select(InventoryItem)
        .where(
            InventoryItem.status == "active",
            InventoryItem.quantity <= InventoryItem.stock_alert_threshold
        )
        .options(selectinload(InventoryItem.category))
        .order_by(InventoryItem.quantity.asc())
        .limit(10)
    )
    low_stock_items = list((await db.execute(low_stock_stmt)).scalars().all())
    low_stock_count = len(low_stock_items)

    # 6. Inventory valuation and total stock units
    val_stmt = select(
        func.sum(InventoryItem.price * InventoryItem.quantity),
        func.sum(InventoryItem.quantity)
    ).where(InventoryItem.status == "active")
    val_result = (await db.execute(val_stmt)).one_or_none()
    inventory_valuation = val_result[0] or 0.0 if val_result else 0.0
    total_stock_units = val_result[1] or 0 if val_result else 0

    # 7. Recent orders (latest 10)
    recent_orders_stmt = (
        select(Order)
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .limit(10)
    )
    recent_orders = list((await db.execute(recent_orders_stmt)).scalars().all())

    # 8. Open support tickets count for sidebar badge
    open_tickets_stmt = select(func.count(SupportTicket.id)).where(SupportTicket.status == "OPEN")
    open_tickets_count = (await db.execute(open_tickets_stmt)).scalar() or 0

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "dashboard",
            "total_revenue": total_revenue,
            "total_orders": total_orders,
            "verified_orders_count": verified_orders_count,
            "pending_verifications_count": pending_verifications_count,
            "pending_orders_count": pending_verifications_count,
            "low_stock_count": low_stock_count,
            "low_stock_items": low_stock_items,
            "inventory_valuation": inventory_valuation,
            "total_stock_units": total_stock_units,
            "recent_orders": recent_orders,
            "open_tickets_count": open_tickets_count,
            "currency": "IDR"
        }
    )
