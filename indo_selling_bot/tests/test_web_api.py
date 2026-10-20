"""
Integration tests for FastAPI Admin Panel endpoints, authentication, and security headers.
"""
import pytest
from httpx import AsyncClient
from indo_selling_bot.config import settings
from indo_selling_bot.security.csrf import generate_csrf_token

@pytest.mark.asyncio
async def test_security_headers_present(async_client: AsyncClient):
    resp = await async_client.get("/login")
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "strict-origin-when-cross-origin" in resp.headers.get("referrer-policy", "")

@pytest.mark.asyncio
async def test_login_flow(async_client: AsyncClient):
    # 1. GET /login gets page
    resp = await async_client.get("/login")
    assert resp.status_code == 200
    assert "Admin Login" in resp.text

    # 2. POST /login with invalid credentials
    csrf = generate_csrf_token("login_form")
    bad_login = await async_client.post(
        "/login",
        data={"username": "testadmin", "password": "WrongPassword!", "csrf_token": csrf}
    )
    assert bad_login.status_code == 401
    assert "Invalid username or password" in bad_login.text

    # 3. POST /login with valid credentials
    good_login = await async_client.post(
        "/login",
        data={"username": "testadmin", "password": "SecretPass@2026", "csrf_token": csrf},
        follow_redirects=False
    )
    assert good_login.status_code == 303
    assert good_login.headers["location"] == "/dashboard"
    assert settings.SESSION_COOKIE_NAME in good_login.cookies

@pytest.mark.asyncio
async def test_protected_routes_require_auth(async_client: AsyncClient):
    # Unauthenticated request to /dashboard must redirect to /login
    resp = await async_client.get("/dashboard", headers={"accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

@pytest.mark.asyncio
async def test_authenticated_dashboard(async_client: AsyncClient, admin_session_cookie: str):
    cookies = {settings.SESSION_COOKIE_NAME: admin_session_cookie}
    resp = await async_client.get("/dashboard", cookies=cookies)
    assert resp.status_code == 200
    assert "Dashboard &amp; Sales Analytics" in resp.text
    assert "Total Revenue" in resp.text
    assert "Total Orders" in resp.text

@pytest.mark.asyncio
async def test_authenticated_inventory_views(async_client: AsyncClient, admin_session_cookie: str):
    cookies = {settings.SESSION_COOKIE_NAME: admin_session_cookie}

    # List inventory
    resp = await async_client.get("/inventory", cookies=cookies)
    assert resp.status_code == 200
    assert "Inventory Records" in resp.text
    assert "TEST-LIC-D1" in resp.text

    # Export CSV
    csv_resp = await async_client.get("/inventory/export/csv", cookies=cookies)
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "TEST-LIC-D1" in csv_resp.text

@pytest.mark.asyncio
async def test_authenticated_admin_pages(async_client: AsyncClient, admin_session_cookie: str):
    cookies = {settings.SESSION_COOKIE_NAME: admin_session_cookie}

    for path in ["/categories", "/orders", "/support", "/audit", "/settings"]:
        resp = await async_client.get(path, cookies=cookies)
        assert resp.status_code == 200, f"Route {path} failed with code {resp.status_code}"

@pytest.mark.asyncio
async def test_logout(async_client: AsyncClient, admin_session_cookie: str):
    cookies = {settings.SESSION_COOKIE_NAME: admin_session_cookie}
    resp = await async_client.get("/logout", cookies=cookies, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"
