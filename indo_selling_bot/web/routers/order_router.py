"""
Order review and authorized manual payment verification router.
Enforces compliance: Never release goods without authorized verification;
Prevents duplicate fulfillment; Notifies customer via Telegram upon verification.
"""
from fastapi import APIRouter, Request, Response, Form, Depends, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.database import get_db
from indo_selling_bot.models.order import Order, OrderStatus
from indo_selling_bot.services.order_service import OrderService
from indo_selling_bot.services.csv_service import CSVService
from indo_selling_bot.bot.notifications import send_customer_telegram_message
from indo_selling_bot.web.dependencies import get_current_admin, verify_csrf_form

order_router = APIRouter(prefix="/orders")

@order_router.get("", response_class=HTMLResponse)
async def list_orders(
    request: Request,
    page: int = Query(1, ge=1),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = None,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates

    orders, total_count, total_pages = await OrderService.list_orders(
        db=db,
        status=status_filter,
        search=search,
        page=page,
        per_page=15
    )

    # Count pending orders for badge
    pending_stmt = select(func.count(Order.id)).where(Order.status == OrderStatus.PAYMENT_SUBMITTED)
    pending_count = (await db.execute(pending_stmt)).scalar_one() or 0

    return templates.TemplateResponse(
        request=request,
        name="orders_list.html",
        context={
            "active_page": "orders",
            "orders": orders,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "search": search,
            "selected_status": status_filter,
            "pending_orders_count": pending_count,
            "alert_success": request.query_params.get("msg"),
            "alert_error": request.query_params.get("err")
        }
    )

@order_router.get("/{order_id}", response_class=HTMLResponse)
async def view_order(
    request: Request,
    order_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    order = await OrderService.get_by_id(db, order_id)
    if not order:
        return RedirectResponse(url="/orders?err=Order+not+found", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="order_detail.html",
        context={
            "active_page": "orders",
            "order": order,
            "alert_success": request.query_params.get("msg"),
            "alert_error": request.query_params.get("err")
        }
    )

@order_router.post("/{order_id}/verify")
async def verify_order(
    request: Request,
    order_id: int,
    notes: str | None = Form(None),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Authorized manual payment verification & automatic deliverable release."""
    admin_user = admin.get("usr", "admin")
    success, msg, order = await OrderService.verify_and_fulfill(
        db=db,
        order_id=order_id,
        admin_user=admin_user,
        verification_notes=notes
    )
    if not success or not order:
        return RedirectResponse(
            url=f"/orders/{order_id}?err={msg.replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    await db.commit()

    # Dispatch release notification to customer's Telegram chat
    notify_text = (
        f"🎉 *PAYMENT VERIFIED & ORDER FULFILLED!*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧾 *Order Code:* `{order.order_code}`\n"
        f"✅ *Verified By:* Admin `{admin_user}`\n\n"
        f"📦 *YOUR DELIVERABLES:*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{order.fulfillment_data}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Thank you for your business!"
    )
    await send_customer_telegram_message(order.telegram_user_id, notify_text)

    return RedirectResponse(
        url=f"/orders/{order_id}?msg=Payment+verified+and+deliverables+released",
        status_code=status.HTTP_303_SEE_OTHER
    )

@order_router.post("/{order_id}/reject")
async def reject_order(
    request: Request,
    order_id: int,
    reason: str = Form(...),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Reject invalid payment proof and restock reserved items."""
    admin_user = admin.get("usr", "admin")
    order = await OrderService.get_by_id(db, order_id)
    if not order:
        return RedirectResponse(url="/orders?err=Order+not+found", status_code=status.HTTP_303_SEE_OTHER)

    success, msg = await OrderService.reject_payment(
        db=db,
        order_id=order_id,
        admin_user=admin_user,
        reason=reason,
        restock=True
    )
    if not success:
        return RedirectResponse(url=f"/orders/{order_id}?err={msg}", status_code=status.HTTP_303_SEE_OTHER)

    await db.commit()

    # Notify customer of rejection
    notify_text = (
        f"❌ *PAYMENT REJECTED FOR ORDER:* `{order.order_code}`\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Reason: {reason}\n\n"
        f"If you believe this is an error, please reach out via Customer Support."
    )
    await send_customer_telegram_message(order.telegram_user_id, notify_text)

    return RedirectResponse(
        url=f"/orders/{order_id}?msg=Payment+rejected+and+stock+returned",
        status_code=status.HTTP_303_SEE_OTHER
    )

@order_router.get("/export/csv")
async def export_orders_csv(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Download full orders history as CSV."""
    csv_data = await CSVService.export_orders_csv(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="orders_export.csv"'}
    )
