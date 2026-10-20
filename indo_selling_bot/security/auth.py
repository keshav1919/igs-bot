"""
Server-side session management and authentication tokens.
Signs session payloads using HMAC-SHA256 with expiration checks.
"""
import hmac
import hashlib
import json
import base64
import time
import secrets
from typing import Any
from indo_selling_bot.config import settings

# In-memory blacklist for revoked sessions (e.g. upon logout)
_revoked_tokens: set[str] = set()

def _base64_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _base64_decode(data: str) -> bytes:
    padding = 4 - (len(data) % 4)
    if padding < 4:
        data += "=" * padding
    return base64.urlsafe_b64decode(data)

def create_session_token(user_id: int, username: str, role: str) -> str:
    """Create a tamper-proof signed session token."""
    now = int(time.time())
    payload = {
        "uid": user_id,
        "usr": username,
        "rol": role,
        "exp": now + settings.SESSION_MAX_AGE_SECONDS,
        "rnd": secrets.token_hex(8)
    }
    payload_json = json.dumps(payload, separators=(',', ':'), sort_keys=True).encode("utf-8")
    payload_b64 = _base64_encode(payload_json)

    signature = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256
    ).digest()
    sig_b64 = _base64_encode(signature)

    return f"{payload_b64}.{sig_b64}"

def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify session token signature, expiration and revocation."""
    if not token or "." not in token:
        return None

    if token in _revoked_tokens:
        return None

    try:
        payload_b64, sig_b64 = token.split(".", 1)
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256
        ).digest()

        actual_sig = _base64_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        payload_bytes = _base64_decode(payload_b64)
        payload = json.loads(payload_bytes.decode("utf-8"))

        now = int(time.time())
        if payload.get("exp", 0) < now:
            return None

        return payload
    except Exception:
        return None

def revoke_session_token(token: str) -> None:
    """Revoke a session token upon admin logout."""
    if token:
        _revoked_tokens.add(token)
        # Avoid unbounded growth by trimming if too large
        if len(_revoked_tokens) > 10000:
            _revoked_tokens.clear()
