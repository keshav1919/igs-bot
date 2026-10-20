"""
System audit log viewing and CSV export router.
"""
from fastapi import APIRouter, Request, Response, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.database import get_db
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.csv_service import CSVService
from indo_selling_bot.web.dependencies import get_current_admin

audit_router = APIRouter(prefix="/audit")

@audit_router.get("", response_class=HTMLResponse)
async def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1),
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    per_page = 20

    count_stmt = select(func.count(AuditLog.id))
    total_count = (await db.execute(count_stmt)).scalar_one() or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    offset = max(0, (page - 1) * per_page)

    stmt = select(AuditLog).order_by(desc(AuditLog.created_at)).offset(offset).limit(per_page)
    logs = list((await db.execute(stmt)).scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="audit_logs.html",
        context={
            "active_page": "audit",
            "logs": logs,
            "total_count": total_count,
            "total_pages": total_pages,
            "current_page": page
        }
    )

@audit_router.get("/export/csv")
async def export_audit_logs_csv(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Download full audit trail as CSV."""
    csv_data = await CSVService.export_audit_logs_csv(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_logs_export.csv"'}
    )
