"""
Category management router for inventory classifications.
"""
from fastapi import APIRouter, Request, Response, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.database import get_db
from indo_selling_bot.models.category import Category
from indo_selling_bot.models.inventory import InventoryItem
from indo_selling_bot.web.dependencies import get_current_admin, verify_csrf_form

category_router = APIRouter(prefix="/categories")

@category_router.get("", response_class=HTMLResponse)
async def list_categories(
    request: Request,
    admin: dict = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    templates = request.app.state.templates
    stmt = select(Category).order_by(Category.sort_order.asc(), Category.name.asc())
    categories = list((await db.execute(stmt)).scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="categories.html",
        context={
            "active_page": "categories",
            "categories": categories,
            "alert_success": request.query_params.get("msg"),
            "alert_error": request.query_params.get("err")
        }
    )

@category_router.post("/new")
async def create_category(
    request: Request,
    name: str = Form(...),
    slug: str | None = Form(None),
    icon: str = Form("📦"),
    sort_order: int = Form(0),
    description: str | None = Form(None),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    clean_name = name.strip()
    clean_slug = slug.strip().lower() if slug else clean_name.lower().replace(" ", "-").replace("/", "-")

    # Check existing slug or name
    stmt = select(Category).where((Category.name == clean_name) | (Category.slug == clean_slug))
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        return RedirectResponse(
            url=f"/categories?err=Category+name+or+slug+already+exists",
            status_code=status.HTTP_303_SEE_OTHER
        )

    cat = Category(
        name=clean_name,
        slug=clean_slug,
        icon=icon.strip() or "📦",
        sort_order=sort_order,
        description=description.strip() if description else None,
        is_active=True
    )
    db.add(cat)
    await db.commit()
    return RedirectResponse(url="/categories?msg=Category+created+successfully", status_code=status.HTTP_303_SEE_OTHER)

@category_router.post("/{category_id}/delete")
async def delete_category(
    category_id: int,
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf_form),
    db: AsyncSession = Depends(get_db)
) -> Response:
    cat = await db.get(Category, category_id)
    if not cat:
        return RedirectResponse(url="/categories", status_code=status.HTTP_303_SEE_OTHER)

    # Check if category has items
    count_stmt = select(func.count(InventoryItem.id)).where(InventoryItem.category_id == category_id)
    item_count = (await db.execute(count_stmt)).scalar_one() or 0
    if item_count > 0:
        return RedirectResponse(
            url=f"/categories?err=Cannot+delete+category+with+{item_count}+active+inventory+items",
            status_code=status.HTTP_303_SEE_OTHER
        )

    await db.delete(cat)
    await db.commit()
    return RedirectResponse(url="/categories?msg=Category+deleted", status_code=status.HTTP_303_SEE_OTHER)
