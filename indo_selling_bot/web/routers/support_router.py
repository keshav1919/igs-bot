"""
Support router for handling customer tickets and dispatching replies to Telegram.
"""
from fastapi import APIRouter, Request, Response, Form, Depends, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.database import get_db
from indo_selling_bot.models.support import SupportTicket
from indo_selling_bot.services.support_service import SupportService
from indo_selling_bot.bot.notifications import send_customer_telegram_message
from indo_selling_bot.web.dependencies import get_current_admin, verify_csrf_form

support_router = APIRouter(prefix="/support")

@support_router.get("", response_class=HTMLResponse)
async def list_support_tickets(
    request: Request,
    page: int = Query(1, ge=1),
    status_filter: str | None = Query(None, alias="status"),
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    tickets, total_count, total_pages = await SupportService.list_tickets(
        db=db,
        status=status_filter,
        page=page,
        per_page=15
    )

    open_count_stmt = select(func.count(SupportTicket.id)).where(SupportTicket.status == "OPEN")
    open_count = (await db.execute(open_count_stmt)).scalar_one() or 0

    return templates.TemplateResponse(
        request=request,
        name="support_list.html",
        context={
            "active_page": "support",
            "tickets": tickets,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": page,
            "open_tickets_count": open_count,
            "alert_success": request.query_params.get("msg")
        }
    )

@support_router.post("/{ticket_id}/reply")
async def reply_ticket(
    request: Request,
    ticket_id: int,
    admin_reply: str = Form(...),
    resolve: bool = Form(False),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    admin_user = admin.get("usr", "admin")
    ticket = await SupportService.reply_ticket(
        db=db,
        ticket_id=ticket_id,
        admin_reply=admin_reply,
        admin_user=admin_user,
        resolve=resolve
    )
    if not ticket:
        return RedirectResponse(url="/support?err=Ticket+not+found", status_code=status.HTTP_303_SEE_OTHER)

    await db.commit()

    # Forward admin reply to customer on Telegram
    tg_msg = (
        f"💬 *RESPONSE FROM SUPPORT* (Ticket: `{ticket.ticket_code}`)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{admin_reply}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Status: *{ticket.status}*"
    )
    await send_customer_telegram_message(ticket.telegram_user_id, tg_msg)

    return RedirectResponse(url="/support?msg=Reply+sent+to+customer", status_code=status.HTTP_303_SEE_OTHER)
