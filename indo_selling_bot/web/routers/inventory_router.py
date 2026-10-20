"""
Inventory router: CRUD, day-based groupings, CSV import/export, and audit trail.
"""
from fastapi import APIRouter, Request, Response, Form, UploadFile, File, Depends, Query, status
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from indo_selling_bot.config import settings
from indo_selling_bot.database import get_db
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem, InventoryChangeLog
from indo_selling_bot.models.setting import StoreSetting
from indo_selling_bot.services.inventory_service import InventoryService
from indo_selling_bot.services.csv_service import CSVService
from indo_selling_bot.web.dependencies import get_current_admin, verify_csrf_form

inventory_router = APIRouter(prefix="/inventory")

async def _get_configured_day_groups(db: AsyncSession) -> list[str]:
    """Fetch configured day groups from settings or default Day 1 - Day 10."""
    stmt = select(StoreSetting).where(StoreSetting.key == "day_groups")
    setting = (await db.execute(stmt)).scalar_one_or_none()
    if setting and setting.value:
        return [g.strip() for g in setting.value.split(",") if g.strip()]
    return settings.DEFAULT_DAY_GROUPS

@inventory_router.get("", response_class=HTMLResponse)
async def list_inventory(
    request: Request,
    page: int = Query(1, ge=1),
    search: str | None = None,
    category_id: int | None = None,
    day_group: str | None = None,
    status: str | None = None,
    low_stock: bool = False,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates

    # Fetch items with filtering and pagination
    items, total_count, total_pages = await InventoryService.list_items(
        db=db,
        category_id=category_id,
        day_group=day_group,
        status=status,
        search=search,
        low_stock_only=low_stock,
        page=page,
        per_page=15
    )

    # Categories for filter dropdown
    cat_stmt = select(Category).where(Category.is_active == True).order_by(Category.name)
    categories = list((await db.execute(cat_stmt)).scalars().all())

    # Configured day groups
    day_groups = await _get_configured_day_groups(db)

    return templates.TemplateResponse(
        request=request,
        name="inventory_list.html",
        context={
            "active_page": "inventory",
            "items": items,
            "categories": categories,
            "day_groups": day_groups,
            "current_page": page,
            "total_count": total_count,
            "total_pages": total_pages,
            "search": search,
            "selected_category": category_id,
            "selected_day_group": day_group,
            "selected_status": status,
            "low_stock_only": low_stock,
            "alert_success": request.query_params.get("msg")
        }
    )

@inventory_router.get("/new", response_class=HTMLResponse)
async def new_inventory_form(
    request: Request,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    cat_stmt = select(Category).where(Category.is_active == True).order_by(Category.name)
    categories = list((await db.execute(cat_stmt)).scalars().all())
    day_groups = await _get_configured_day_groups(db)

    return templates.TemplateResponse(
        request=request,
        name="inventory_form.html",
        context={
            "active_page": "inventory",
            "item": None,
            "categories": categories,
            "configured_day_groups": day_groups,
            "default_currency": settings.DEFAULT_CURRENCY
        }
    )

@inventory_router.post("/new")
async def create_inventory_item(
    request: Request,
    sku: str = Form(...),
    category_id: int = Form(...),
    title: str = Form(...),
    price: float = Form(...),
    quantity: int = Form(0),
    day_group: str = Form("Day 1"),
    currency: str = Form("IDR"),
    stock_alert_threshold: int = Form(5),
    status: str = Form("active"),
    description: str | None = Form(None),
    fulfillment_payload: str | None = Form(None),
    internal_notes: str | None = Form(None),
    created_at_str: str | None = Form(None),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    # Check if SKU exists
    existing = await InventoryService.get_by_sku(db, sku)
    if existing:
        templates = request.app.state.templates
        cat_stmt = select(Category).where(Category.is_active == True).order_by(Category.name)
        categories = list((await db.execute(cat_stmt)).scalars().all())
        day_groups = await _get_configured_day_groups(db)
        return templates.TemplateResponse(
            request=request,
            name="inventory_form.html",
            context={
                "active_page": "inventory",
                "item": None,
                "categories": categories,
                "configured_day_groups": day_groups,
                "default_currency": currency,
                "alert_error": f"SKU '{sku}' already exists. Please choose a unique SKU."
            },
            status_code=400
        )

    # Parse created_at if provided
    parsed_created_at = None
    if created_at_str and created_at_str.strip():
        try:
            from datetime import datetime, timezone
            clean_str = created_at_str.strip()
            if len(clean_str) == 10:  # YYYY-MM-DD
                parsed_created_at = datetime.strptime(clean_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                parsed_created_at = datetime.fromisoformat(clean_str)
                if parsed_created_at.tzinfo is None:
                    parsed_created_at = parsed_created_at.replace(tzinfo=timezone.utc)
        except Exception:
            parsed_created_at = None

    await InventoryService.create_item(
        db=db,
        sku=sku,
        category_id=category_id,
        title=title,
        price=price,
        quantity=quantity,
        day_group=day_group,
        currency=currency,
        stock_alert_threshold=stock_alert_threshold,
        status=status,
        description=description,
        fulfillment_payload=fulfillment_payload,
        internal_notes=internal_notes,
        created_at=parsed_created_at,
        admin_user=admin.get("usr", "admin")
    )
    await db.commit()
    return RedirectResponse(url="/inventory?msg=Item+created+successfully", status_code=status.HTTP_303_SEE_OTHER)

@inventory_router.get("/{item_id}/edit", response_class=HTMLResponse)
async def edit_inventory_form(
    request: Request,
    item_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    item = await InventoryService.get_by_id(db, item_id)
    if not item:
        return RedirectResponse(url="/inventory", status_code=status.HTTP_303_SEE_OTHER)

    cat_stmt = select(Category).where(Category.is_active == True).order_by(Category.name)
    categories = list((await db.execute(cat_stmt)).scalars().all())
    day_groups = await _get_configured_day_groups(db)

    return templates.TemplateResponse(
        request=request,
        name="inventory_form.html",
        context={
            "active_page": "inventory",
            "item": item,
            "categories": categories,
            "configured_day_groups": day_groups,
            "default_currency": item.currency
        }
    )

@inventory_router.post("/{item_id}/edit")
async def update_inventory_item(
    request: Request,
    item_id: int,
    category_id: int = Form(...),
    title: str = Form(...),
    price: float = Form(...),
    quantity: int = Form(...),
    day_group: str = Form(...),
    stock_alert_threshold: int = Form(...),
    status: str = Form(...),
    description: str | None = Form(None),
    fulfillment_payload: str | None = Form(None),
    internal_notes: str | None = Form(None),
    created_at_str: str | None = Form(None),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    item = await InventoryService.get_by_id(db, item_id)
    if not item:
        return RedirectResponse(url="/inventory", status_code=status.HTTP_303_SEE_OTHER)

    updates = {
        "category_id": category_id,
        "title": title,
        "price": price,
        "quantity": quantity,
        "day_group": day_group,
        "stock_alert_threshold": stock_alert_threshold,
        "status": status,
        "description": description,
        "fulfillment_payload": fulfillment_payload,
        "internal_notes": internal_notes
    }

    if created_at_str and created_at_str.strip():
        try:
            from datetime import datetime, timezone
            clean_str = created_at_str.strip()
            if len(clean_str) == 10:
                parsed_dt = datetime.strptime(clean_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            else:
                parsed_dt = datetime.fromisoformat(clean_str)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            updates["created_at"] = parsed_dt
            # Also align day group to match legitimate calculated age
            updates["day_group"] = InventoryService.calculate_day_group_from_timestamp(parsed_dt)
        except Exception:
            pass

    await InventoryService.update_item(db, item, updates, admin_user=admin.get("usr", "admin"))
    await db.commit()
    return RedirectResponse(url=f"/inventory?msg=Item+{item.sku}+updated", status_code=status.HTTP_303_SEE_OTHER)

@inventory_router.post("/{item_id}/delete")
async def delete_inventory_item(
    item_id: int,
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    await InventoryService.delete_item(db, item_id, admin_user=admin.get("usr", "admin"))
    await db.commit()
    return RedirectResponse(url="/inventory?msg=Item+deleted", status_code=status.HTTP_303_SEE_OTHER)

@inventory_router.post("/{item_id}/toggle-status")
async def toggle_item_status(
    item_id: int,
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Quickly toggle item status between active and paused (deactivate/activate)."""
    item = await InventoryService.get_by_id(db, item_id)
    if not item:
        return RedirectResponse(url="/inventory?err=Item+not+found", status_code=status.HTTP_303_SEE_OTHER)

    admin_user = admin.get("usr", "admin")
    if item.status == "active":
        await InventoryService.deactivate_item(db, item_id, admin_user=admin_user)
        msg = f"Item+{item.sku}+deactivated"
    else:
        await InventoryService.activate_item(db, item_id, admin_user=admin_user)
        msg = f"Item+{item.sku}+activated"

    await db.commit()
    return RedirectResponse(url=f"/inventory?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)

@inventory_router.post("/{item_id}/quick-restock")
async def quick_restock_item(
    item_id: int,
    additional_quantity: int = Form(...),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Quickly add stock to an item without full edit form."""
    if additional_quantity <= 0:
        return RedirectResponse(url="/inventory?err=Restock+quantity+must+be+greater+than+0", status_code=status.HTTP_303_SEE_OTHER)

    admin_user = admin.get("usr", "admin")
    updated = await InventoryService.quick_restock(db, item_id, additional_quantity, admin_user=admin_user)
    if not updated:
        return RedirectResponse(url="/inventory?err=Item+not+found", status_code=status.HTTP_303_SEE_OTHER)

    await db.commit()
    return RedirectResponse(url=f"/inventory?msg=Restocked+{updated.sku}+by+{additional_quantity}+units", status_code=status.HTTP_303_SEE_OTHER)

@inventory_router.get("/{item_id}/history", response_class=HTMLResponse)
async def view_item_history(
    request: Request,
    item_id: int,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    item = await InventoryService.get_by_id(db, item_id)
    if not item:
        return RedirectResponse(url="/inventory", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request=request,
        name="inventory_history.html",
        context={
            "active_page": "inventory",
            "item": item,
            "history": item.change_logs
        }
    )

@inventory_router.get("/export/csv")
async def export_csv(
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Download full inventory records as CSV."""
    csv_data = await CSVService.export_inventory_csv(db)
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory_export.csv"'}
    )

@inventory_router.get("/template/csv")
async def download_sample_template() -> Response:
    """Download sample CSV template for importing inventory."""
    csv_data = CSVService.get_inventory_sample_template()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory_import_template.csv"'}
    )

@inventory_router.post("/import/csv")
async def import_csv_file(
    request: Request,
    csv_file: UploadFile = File(...),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Process uploaded CSV file for inventory items."""
    content = await csv_file.read()
    csv_text = content.decode("utf-8-sig", errors="replace")

    result = await CSVService.import_inventory_csv(
        db=db,
        csv_content=csv_text,
        admin_user=admin.get("usr", "admin")
    )
    await db.commit()

    if not result["success"]:
        return RedirectResponse(url=f"/inventory?err={result.get('error', 'Import failed')}", status_code=status.HTTP_303_SEE_OTHER)

    msg = f"CSV+Imported:+{result['created']}+created,+{result['updated']}+updated"
    return RedirectResponse(url=f"/inventory?msg={msg}", status_code=status.HTTP_303_SEE_OTHER)
