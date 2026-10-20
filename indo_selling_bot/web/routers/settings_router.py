"""
Settings router for store parameters, day groups configuration, and payment accounts.
"""
import json
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Response, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.database import get_db
from indo_selling_bot.models.setting import StoreSetting
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.services.payment_service import PaymentService, DEFAULT_PAYMENT_CHANNELS
from indo_selling_bot.web.dependencies import get_current_admin, verify_csrf_form

settings_router = APIRouter(prefix="/settings")

@settings_router.get("", response_class=HTMLResponse)
async def view_settings(
    request: Request,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates

    # Load Day Groups
    dg_stmt = select(StoreSetting).where(StoreSetting.key == "day_groups")
    dg_setting = (await db.execute(dg_stmt)).scalar_one_or_none()
    day_groups_str = dg_setting.value if dg_setting else ", ".join(settings.DEFAULT_DAY_GROUPS)

    # Load Currency
    curr_stmt = select(StoreSetting).where(StoreSetting.key == "currency")
    curr_setting = (await db.execute(curr_stmt)).scalar_one_or_none()
    currency = curr_setting.value if curr_setting else settings.DEFAULT_CURRENCY

    # Load Threshold
    thresh_stmt = select(StoreSetting).where(StoreSetting.key == "stock_alert_threshold")
    thresh_setting = (await db.execute(thresh_stmt)).scalar_one_or_none()
    threshold = thresh_setting.value if thresh_setting else str(settings.DEFAULT_STOCK_ALERT_THRESHOLD)

    # Load Payment Methods
    methods = await PaymentService.get_payment_methods(db)
    payment_methods_json = json.dumps(methods, indent=2)

    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "active_page": "settings",
            "day_groups_str": day_groups_str,
            "currency": currency,
            "stock_alert_threshold": threshold,
            "payment_methods_json": payment_methods_json,
            "alert_success": request.query_params.get("msg"),
            "alert_error": request.query_params.get("err")
        }
    )

@settings_router.post("")
async def save_settings(
    request: Request,
    day_groups: str = Form(...),
    currency: str = Form(...),
    stock_alert_threshold: int = Form(...),
    payment_methods_json: str = Form(...),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    # Validate payment methods JSON
    try:
        parsed_methods = json.loads(payment_methods_json)
        if not isinstance(parsed_methods, list):
            raise ValueError("Payment methods must be a JSON array of channel objects.")
    except Exception as e:
        return RedirectResponse(
            url=f"/settings?err=Invalid+JSON+for+payment+methods:+{str(e).replace(' ', '+')}",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # Helper to upsert StoreSetting
    async def _upsert_setting(k: str, v: str, desc: str):
        stmt = select(StoreSetting).where(StoreSetting.key == k)
        row = (await db.execute(stmt)).scalar_one_or_none()
        if not row:
            row = StoreSetting(key=k, value=v, description=desc)
            db.add(row)
        else:
            row.value = v
            row.updated_at = datetime.now(timezone.utc)

    await _upsert_setting("day_groups", day_groups.strip(), "Configured day-based grouping tiers")
    await _upsert_setting("currency", currency.strip().upper(), "Store default currency code")
    await _upsert_setting("stock_alert_threshold", str(stock_alert_threshold), "Global stock alert warning threshold")
    await _upsert_setting("payment_methods", json.dumps(parsed_methods, indent=2), "Store payment channels and instructions")

    # Audit log
    audit = AuditLog(
        action="SETTINGS_UPDATE",
        performed_by=admin.get("usr", "admin"),
        details="Updated day groups, currency, stock threshold and payment channels",
        created_at=datetime.now(timezone.utc)
    )
    db.add(audit)
    await db.commit()

    return RedirectResponse(url="/settings?msg=Settings+saved+successfully", status_code=status.HTTP_303_SEE_OTHER)
