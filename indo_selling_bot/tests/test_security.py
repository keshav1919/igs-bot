"""
Unit tests for security modules: Argon2, Session signing, CSRF, and Rate limiting.
"""
import pytest
import time
from indo_selling_bot.security.hashing import hash_password, verify_password
from indo_selling_bot.security.auth import create_session_token, verify_session_token, revoke_session_token
from indo_selling_bot.security.csrf import generate_csrf_token, verify_csrf_token
from indo_selling_bot.security.rate_limiter import SlidingWindowRateLimiter

def test_argon2_hashing_and_verification():
    raw_password = "SuperSecurePassword@2026!"
    hashed = hash_password(raw_password)

    # Must start with standard Argon2id prefix
    assert hashed.startswith("$argon2id$")
    assert verify_password(raw_password, hashed) is True
    assert verify_password("WrongPassword123", hashed) is False
    assert verify_password("", hashed) is False
    assert verify_password(raw_password, "") is False

def test_argon2_empty_password():
    with pytest.raises(ValueError):
        hash_password("")

def test_session_token_lifecycle():
    token = create_session_token(user_id=42, username="moderator", role="admin")
    assert "." in token

    payload = verify_session_token(token)
    assert payload is not None
    assert payload["uid"] == 42
    assert payload["usr"] == "moderator"
    assert payload["rol"] == "admin"
    assert payload["exp"] > time.time()

def test_session_token_tampering():
    token = create_session_token(user_id=42, username="moderator", role="admin")
    payload_b64, sig_b64 = token.split(".", 1)

    # Tamper with signature
    tampered_sig = sig_b64[:-2] + "AA"
    tampered_token = f"{payload_b64}.{tampered_sig}"
    assert verify_session_token(tampered_token) is None

def test_session_token_revocation():
    token = create_session_token(user_id=1, username="admin", role="superadmin")
    assert verify_session_token(token) is not None

    revoke_session_token(token)
    assert verify_session_token(token) is None

def test_csrf_token_validation():
    session_id = "test_session_xyz_123"
    csrf = generate_csrf_token(session_id)
    assert len(csrf) == 64  # SHA-256 hex string

    # Matching session
    assert verify_csrf_token(csrf, session_id) is True

    # Tampered CSRF
    assert verify_csrf_token("invalid_token", session_id) is False

    # Different session
    assert verify_csrf_token(csrf, "different_session_456") is False

def test_rate_limiter_sliding_window():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=2)
    key = "ip_192.168.1.10"

    # First 3 attempts should be allowed
    assert limiter.is_allowed(key)[0] is True
    limiter.record_attempt(key)
    assert limiter.is_allowed(key)[0] is True
    limiter.record_attempt(key)
    assert limiter.is_allowed(key)[0] is True
    limiter.record_attempt(key)

    # 4th attempt must be blocked
    allowed, retry_after = limiter.is_allowed(key)
    assert allowed is False
    assert retry_after > 0

    # Reset allows again
    limiter.reset(key)
    assert limiter.is_allowed(key)[0] is True
