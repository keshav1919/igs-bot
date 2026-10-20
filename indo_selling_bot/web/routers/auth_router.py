"""
Authentication router: Login, Logout, Argon2 verification, and Session management.
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Response, Form, Depends, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.database import get_db
from indo_selling_bot.models.user import User
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.security.hashing import verify_password
from indo_selling_bot.security.auth import create_session_token, revoke_session_token
from indo_selling_bot.security.csrf import generate_csrf_token, verify_csrf_token
from indo_selling_bot.security.rate_limiter import login_rate_limiter

auth_router = APIRouter()

@auth_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    """Render the dark responsive login form with CSRF token."""
    # Check if already authenticated
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_token:
        from indo_selling_bot.security.auth import verify_session_token
        if verify_session_token(session_token):
            return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)

    templates = request.app.state.templates
    csrf_token = generate_csrf_token("login_form")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf_token, "error": None}
    )

@auth_router.post("/login", response_class=HTMLResponse)
async def process_login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(get_db)
) -> Response:
    """Validate credentials with Argon2 and issue HTTP-only signed session cookie."""
    templates = request.app.state.templates
    client_ip = request.client.host if request.client else "127.0.0.1"

    # Rate Limiting Check
    allowed, retry_after = login_rate_limiter.is_allowed(client_ip)
    if not allowed:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": generate_csrf_token("login_form"),
                "error": f"Too many login attempts. Please wait {retry_after} seconds before retrying."
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS
        )

    # CSRF Check
    if not verify_csrf_token(csrf_token, "login_form"):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": generate_csrf_token("login_form"),
                "error": "Security validation failed. Please refresh and try again."
            },
            status_code=status.HTTP_400_BAD_REQUEST
        )

    # Query User
    stmt = select(User).where(User.username == username.strip(), User.is_active == True)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        login_rate_limiter.record_attempt(client_ip)
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "csrf_token": generate_csrf_token("login_form"),
                "error": "Invalid username or password."
            },
            status_code=status.HTTP_401_UNAUTHORIZED
        )

    # Login Success - Reset rate limit attempts
    login_rate_limiter.reset(client_ip)

    # Update last login
    user.last_login = datetime.now(timezone.utc)

    # Create Audit Log
    audit = AuditLog(
        action="ADMIN_LOGIN",
        performed_by=user.username,
        ip_address=client_ip,
        details="Admin logged in successfully",
        created_at=datetime.now(timezone.utc)
    )
    db.add(audit)
    await db.commit()

    # Generate Signed Session Token
    session_token = create_session_token(
        user_id=user.id,
        username=user.username,
        role=user.role
    )

    redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=not settings.DEBUG
    )
    return redirect

@auth_router.get("/logout")
async def process_logout(request: Request) -> Response:
    """Revoke admin session and clear cookie."""
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if session_token:
        revoke_session_token(session_token)

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.SESSION_COOKIE_NAME)
    return redirect
