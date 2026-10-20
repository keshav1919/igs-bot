"""
CSRF token generator and validator.
Binds CSRF tokens cryptographically to the admin session token.
"""
import hmac
import hashlib
from indo_selling_bot.config import settings

def generate_csrf_token(session_token: str) -> str:
    """Generate a CSRF token bound to the current session."""
    if not session_token:
        session_token = "anonymous"
    
    mac = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"csrf:{session_token}".encode("utf-8"),
        hashlib.sha256
    )
    return mac.hexdigest()

def verify_csrf_token(csrf_token: str | None, session_token: str | None) -> bool:
    """Verify that the provided CSRF token matches the session."""
    if not csrf_token or not session_token:
        return False
    expected = generate_csrf_token(session_token)
    return hmac.compare_digest(csrf_token, expected)
