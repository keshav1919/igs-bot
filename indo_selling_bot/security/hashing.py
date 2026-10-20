"""
Argon2 password hashing and verification module.
Uses argon2-cffi with recommended secure memory and time cost parameters.
"""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

# Argon2id password hasher with tuned parameters for high security
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,  # 64 MB
    parallelism=4,
    hash_len=32,
    salt_len=16
)

def hash_password(password: str) -> str:
    """Hash a plaintext password using Argon2id."""
    if not password:
        raise ValueError("Password cannot be empty")
    return _hasher.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against an Argon2id hash."""
    if not password or not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

def check_needs_rehash(password_hash: str) -> bool:
    """Check if the hash needs to be upgraded to newer parameters."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False
