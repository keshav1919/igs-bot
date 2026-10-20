"""
FastAPI application for Indo Commerce Admin Panel.
Configures dark theme templates, security headers, routers, and database initialization.
"""
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from indo_selling_bot.config import settings
from indo_selling_bot.database import init_db
from indo_selling_bot.web.routers.auth_router import auth_router
from indo_selling_bot.web.routers.dashboard import dashboard_router
from indo_selling_bot.web.routers.inventory_router import inventory_router
from indo_selling_bot.web.routers.category_router import category_router
from indo_selling_bot.web.routers.order_router import order_router
from indo_selling_bot.web.routers.support_router import support_router
from indo_selling_bot.web.routers.audit_router import audit_router
from indo_selling_bot.web.routers.settings_router import settings_router

BASE_DIR = Path(__file__).resolve().parent

def format_currency_filter(val: float | int | None, currency: str = "IDR") -> str:
    """Format numbers cleanly with currency prefix/suffix."""
    if val is None:
        val = 0.0
    try:
        num = float(val)
        if currency.upper() == "IDR":
            return f"Rp {num:,.0f}"
        elif currency.upper() == "USD":
            return f"${num:,.2f}"
        return f"{num:,.2f} {currency}"
    except (ValueError, TypeError):
        return f"{val} {currency}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables and pragmas
    await init_db()
    try:
        from indo_selling_bot.seed_data import seed_all
        await seed_all()
    except Exception as e:
        import logging
        logging.getLogger("uvicorn").warning(f"[*] Baseline database check note: {e}")
    yield

def create_app() -> FastAPI:
    """Application factory for FastAPI admin panel."""
    app = FastAPI(
        title=settings.APP_NAME,
        description="Premium Dark Web Admin Panel & Inventory Management",
        version="2.0.0",
        docs_url="/api/docs" if settings.DEBUG else None,
        redoc_url=None,
        lifespan=lifespan
    )

    # Mount static assets
    static_dir = BASE_DIR / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Configure Jinja2 Templates
    templates_dir = BASE_DIR / "templates"
    templates = Jinja2Templates(directory=templates_dir)
    templates.env.filters["format_currency"] = format_currency_filter
    app.state.templates = templates

    # Security Headers Middleware
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response

    # Custom Error Handlers (Dark Theme)
    @app.exception_handler(404)
    async def custom_404_handler(request: Request, exc: Exception):
        accept = request.headers.get("accept", "")
        if "application/json" in accept or request.url.path.startswith("/api"):
            detail = getattr(exc, "detail", "Not Found")
            return JSONResponse(status_code=404, content={"detail": detail})
        detail = getattr(exc, "detail", "Resource not found")
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={"request": request, "detail": detail},
            status_code=404,
        )

    @app.exception_handler(500)
    async def custom_500_handler(request: Request, exc: Exception):
        accept = request.headers.get("accept", "")
        if "application/json" in accept or request.url.path.startswith("/api"):
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})
        return templates.TemplateResponse(
            request=request,
            name="500.html",
            context={"request": request, "detail": "An unexpected internal server error occurred."},
            status_code=500,
        )

    # Include Routers
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(inventory_router)
    app.include_router(category_router)
    app.include_router(order_router)
    app.include_router(support_router)
    app.include_router(audit_router)
    app.include_router(settings_router)

    # Health Check endpoints for Render / cloud health monitors
    @app.get("/healthz", tags=["Health"])
    @app.get("/health", tags=["Health"])
    async def health_check():
        is_token_valid, reason = settings.validate_bot_token(settings.TELEGRAM_BOT_TOKEN)
        bot_running = getattr(app.state, "bot_running", False)
        bot_error = getattr(app.state, "bot_error", None)
        return {
            "status": "healthy",
            "service": settings.APP_NAME,
            "version": "2.0.0",
            "bot_status": {
                "polling_active": bot_running,
                "token_valid": is_token_valid,
                "token_masked": settings.mask_token(settings.TELEGRAM_BOT_TOKEN),
                "token_details": reason,
                "last_error": bot_error
            }
        }

    return app

app = create_app()
