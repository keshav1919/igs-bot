"""
FastAPI web dependencies for authentication, CSRF validation, and rate limiting.
"""
from typing import Annotated
from fastapi import Request, HTTPException, Depends, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.database import get_db
from indo_selling_bot.security.auth import verify_session_token
from indo_selling_bot.security.csrf import verify_csrf_token, generate_csrf_token
from indo_selling_bot.security.rate_limiter import login_rate_limiter

def get_current_admin(request: Request) -> dict:
    """
    Validate admin session cookie.
    Redirects to /login if unauthenticated on web page, or raises 401 for API.
    """
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_token:
        # Check if browser request
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"}
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required."
        )

    payload = verify_session_token(session_token)
    if not payload:
        if "text/html" in request.headers.get("accept", ""):
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"}
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalid or expired."
        )

    # Attach CSRF token to request state for convenient template rendering
    request.state.csrf_token = generate_csrf_token(session_token)
    request.state.admin_user = payload.get("usr", "admin")
    request.state.admin_role = payload.get("rol", "admin")
    request.state.admin_id = payload.get("uid", 1)

    return payload

async def verify_csrf_form(request: Request) -> None:
    """Validate CSRF token on POST form requests."""
    session_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not session_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing session for CSRF check.")

    form_data = await request.form()
    csrf_token = form_data.get("csrf_token") or request.headers.get("X-CSRF-Token")

    if not verify_csrf_token(csrf_token, session_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed. Form expired.")

def check_login_rate_limit(request: Request) -> None:
    """Enforce rate limits on login attempts based on client IP."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    allowed, retry_after = login_rate_limiter.is_allowed(client_ip)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Please try again in {retry_after} seconds."
        )
