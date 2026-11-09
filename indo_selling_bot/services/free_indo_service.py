"""
Free Indo IGS Service.
Core engine for proxy verification, duplicate prevention, HMAC fingerprinting,
credential encryption at rest, rate-limiting & anti-spam, reward delivery, and stored proxy retrieval.
"""
import asyncio
import base64
import hashlib
import hmac
import html
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple, List, Dict, Any

import aiohttp
from cryptography.fernet import Fernet
from sqlalchemy import select, update, func, desc, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.free_indo_igs import (
    FreeIndoSettings,
    ProxySubmission,
    ProxyRateLimit,
    UserRewardProgress,
    RewardTransaction,
)
from indo_selling_bot.services.stock_txt_service import StockTxtService

logger = logging.getLogger("free_indo_service")


class FreeIndoService:
    _reward_lock = asyncio.Lock()

    # -------------------------------------------------------------
    # Cryptographic & Fingerprint Helpers
    # -------------------------------------------------------------
    @classmethod
    def _get_encryption_cipher(cls) -> Fernet:
        """Derive standard Fernet key securely from server SECRET_KEY."""
        secret_bytes = settings.SECRET_KEY.encode("utf-8")
        key = base64.urlsafe_b64encode(hashlib.sha256(secret_bytes).digest())
        return Fernet(key)

    @classmethod
    def encrypt_password(cls, password: str) -> str:
        """Encrypt proxy password for storage at rest."""
        if not password:
            return ""
        cipher = cls._get_encryption_cipher()
        return cipher.encrypt(password.encode("utf-8")).decode("utf-8")

    @classmethod
    def decrypt_password(cls, token: str) -> str:
        """Decrypt proxy password for authorized administrative retrieval."""
        if not token:
            return ""
        try:
            cipher = cls._get_encryption_cipher()
            return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to decrypt stored password: {e}")
            return "[DECRYPTION_ERROR]"

    @classmethod
    def generate_fingerprint(cls, host: str, port: int, username: str) -> str:
        """
        Generate tamper-proof HMAC-SHA256 fingerprint for unique proxy identification.
        Normalizes host, port, and username.
        """
        h = host.strip().lower()
        if h in ("pr.oxy.labs.io", "pr.oxylabs.io"):
            h = "pr.oxylabs.io"
        secret = settings.SECRET_KEY.encode("utf-8")
        canonical = f"{h}:{int(port)}:{username.strip().lower()}"
        return hmac.new(secret, canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    # -------------------------------------------------------------
    # Settings Management
    # -------------------------------------------------------------
    @classmethod
    async def get_settings(cls, db: AsyncSession) -> FreeIndoSettings:
        """Fetch or initialize singleton settings record."""
        stmt = select(FreeIndoSettings).where(FreeIndoSettings.id == 1)
        res = (await db.execute(stmt)).scalar_one_or_none()
        if not res:
            res = FreeIndoSettings(
                id=1,
                enabled=True,
                proxy_host="pr.oxylabs.io",
                proxy_port=7777,
                connection_timeout=15,
                max_failed_attempts=5,
                timeout_minutes=20,
                tutorial_file_id=None
            )
            db.add(res)
            await db.commit()
            await db.refresh(res)
        elif res.proxy_host == "pr.oxy.labs.io":
            res.proxy_host = "pr.oxylabs.io"
            await db.commit()
            await db.refresh(res)
        return res

    @classmethod
    async def update_settings(cls, db: AsyncSession, **kwargs) -> FreeIndoSettings:
        """Update configurable settings."""
        cfg = await cls.get_settings(db)
        for key, value in kwargs.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)
        cfg.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(cfg)
        return cfg

    # -------------------------------------------------------------
    # Rate Limiting & Anti-Spam (UTC DB timestamps)
    # -------------------------------------------------------------
    @classmethod
    async def check_user_timeout(
        cls,
        db: AsyncSession,
        telegram_user_id: int,
        is_admin: bool = False
    ) -> Tuple[bool, int, int]:
        """
        Check if user is currently restricted.
        Admins are unconditionally exempt.
        Returns: (is_restricted: bool, remaining_minutes: int, remaining_seconds: int)
        """
        if is_admin:
            return False, 0, 0

        now_utc = datetime.now(timezone.utc)
        stmt = select(ProxyRateLimit).where(ProxyRateLimit.telegram_user_id == telegram_user_id)
        rl = (await db.execute(stmt)).scalar_one_or_none()
        if not rl or not rl.timeout_until:
            return False, 0, 0

        timeout_dt = rl.timeout_until
        if timeout_dt.tzinfo is None:
            timeout_dt = timeout_dt.replace(tzinfo=timezone.utc)

        if timeout_dt > now_utc:
            remaining_seconds = int((timeout_dt - now_utc).total_seconds())
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            return True, remaining_minutes, remaining_seconds

        # Timeout has expired; auto-clear timeout_until
        rl.timeout_until = None
        rl.consecutive_failures = 0
        rl.updated_at = now_utc
        await db.commit()
        return False, 0, 0

    @classmethod
    async def record_failure(
        cls,
        db: AsyncSession,
        telegram_user_id: int,
        is_admin: bool = False,
        max_fails: int = 5,
        timeout_minutes: int = 20
    ) -> Tuple[bool, int, int]:
        """
        Atomically increment failure counter for user.
        Admins are completely exempt from counters and timeouts.
        Returns: (is_newly_timed_out: bool, timeout_minutes: int, total_failures: int)
        """
        if is_admin:
            return False, 0, 0

        now_utc = datetime.now(timezone.utc)
        stmt = select(ProxyRateLimit).where(ProxyRateLimit.telegram_user_id == telegram_user_id)
        rl = (await db.execute(stmt)).scalar_one_or_none()

        if not rl:
            rl = ProxyRateLimit(
                telegram_user_id=telegram_user_id,
                consecutive_failures=1,
                timeout_until=None,
                updated_at=now_utc
            )
            db.add(rl)
            await db.flush()
        else:
            rl.consecutive_failures += 1
            rl.updated_at = now_utc

        if rl.consecutive_failures >= max_fails:
            rl.timeout_until = now_utc + timedelta(minutes=timeout_minutes)
            await db.commit()
            return True, timeout_minutes, rl.consecutive_failures

        await db.commit()
        return False, 0, rl.consecutive_failures

    @classmethod
    async def reset_failures(cls, db: AsyncSession, telegram_user_id: int) -> None:
        """Reset consecutive failure counter on successful verification."""
        stmt = select(ProxyRateLimit).where(ProxyRateLimit.telegram_user_id == telegram_user_id)
        rl = (await db.execute(stmt)).scalar_one_or_none()
        if rl:
            rl.consecutive_failures = 0
            rl.timeout_until = None
            rl.updated_at = datetime.now(timezone.utc)
            await db.commit()

    @classmethod
    async def clear_timeout(cls, db: AsyncSession, telegram_user_id: int) -> bool:
        """Manually remove user timeout (Admin action)."""
        stmt = select(ProxyRateLimit).where(ProxyRateLimit.telegram_user_id == telegram_user_id)
        rl = (await db.execute(stmt)).scalar_one_or_none()
        if rl:
            rl.consecutive_failures = 0
            rl.timeout_until = None
            rl.updated_at = datetime.now(timezone.utc)
            await db.commit()
            return True
        return False

    @classmethod
    async def get_active_timeout_users(cls, db: AsyncSession) -> List[Dict[str, Any]]:
        """Fetch list of users currently under timeout."""
        now_utc = datetime.now(timezone.utc)
        stmt = (
            select(ProxyRateLimit)
            .where(ProxyRateLimit.timeout_until > now_utc)
            .order_by(ProxyRateLimit.timeout_until.asc())
        )
        records = (await db.execute(stmt)).scalars().all()
        results = []
        for r in records:
            timeout_dt = r.timeout_until
            if timeout_dt.tzinfo is None:
                timeout_dt = timeout_dt.replace(tzinfo=timezone.utc)
            rem_secs = max(0, int((timeout_dt - now_utc).total_seconds()))
            rem_mins = max(1, (rem_secs + 59) // 60)
            results.append({
                "telegram_user_id": r.telegram_user_id,
                "consecutive_failures": r.consecutive_failures,
                "timeout_until": timeout_dt,
                "remaining_minutes": rem_mins,
                "remaining_seconds": rem_secs,
            })
        return results

    # -------------------------------------------------------------
    # Duplicate Verification Prevention
    # -------------------------------------------------------------
    @classmethod
    async def is_proxy_already_approved(cls, db: AsyncSession, fingerprint: str) -> bool:
        """Check if this proxy has already been approved globally across all users."""
        stmt = (
            select(ProxySubmission.id)
            .where(
                ProxySubmission.credential_fingerprint == fingerprint,
                ProxySubmission.status == "APPROVED"
            )
            .limit(1)
        )
        res = (await db.execute(stmt)).scalar_one_or_none()
        return res is not None

    # -------------------------------------------------------------
    # Asynchronous Proxy Verification Client
    # -------------------------------------------------------------
    @classmethod
    async def verify_proxy_connection(
        cls,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout_seconds: int = 15
    ) -> Tuple[str, Optional[float], Optional[str]]:
        """
        Verify HTTP proxy credentials against an external HTTPS test endpoint.
        Uses aiohttp with BasicAuth.
        Statuses:
        - 'APPROVED': Proxy authenticated and connected successfully.
        - 'REJECTED': Proxy authentication failure (HTTP 407) or confirmed bad credentials.
        - 'TIMEOUT': Proxy server timed out.
        - 'TEMPORARY_ERROR': Network unreachable, provider outage, or endpoint error.
        Returns: (status: str, response_time_ms: float | None, error_details: str | None)
        """
        h = host.strip().lower()
        if h in ("pr.oxy.labs.io", "pr.oxylabs.io"):
            h = "pr.oxylabs.io"

        test_endpoints = [
            "https://api.ipify.org?format=json",
            "https://httpbin.org/ip",
            "http://ip-api.com/json"
        ]
        proxy_url = f"http://{h}:{port}"
        auth = aiohttp.BasicAuth(login=username, password=password)

        start_time = time.perf_counter()
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=min(8, timeout_seconds))

        last_error = None
        for test_url in test_endpoints:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(test_url, proxy=proxy_url, proxy_auth=auth) as resp:
                        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                        if resp.status == 200:
                            return "APPROVED", round(elapsed_ms, 2), None

                        # If the proxy itself returned 407 (Proxy Authentication Required)
                        if resp.status == 407:
                            return "REJECTED", round(elapsed_ms, 2), "HTTP 407 Proxy Authentication Required"

                        # If destination website returned 403 or 404 (Cloudflare bot block, etc.)
                        # per spec: "Do not treat HTTP 403 or 404 from the destination website as automatic proxy failure"
                        if resp.status in (403, 404):
                            return "APPROVED", round(elapsed_ms, 2), f"Connected (Target returned HTTP {resp.status})"

                        # Other unexpected HTTP status
                        return "APPROVED", round(elapsed_ms, 2), f"Connected with HTTP {resp.status}"

            except aiohttp.ClientHttpProxyError as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                if getattr(e, "status", None) == 407 or getattr(e, "code", None) == 407:
                    return "REJECTED", round(elapsed_ms, 2), "HTTP 407 Proxy Authentication Failed"
                return "REJECTED", round(elapsed_ms, 2), f"Proxy HTTP Error: {e}"

            except asyncio.TimeoutError:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return "TIMEOUT", round(elapsed_ms, 2), f"Proxy connection timed out after {timeout_seconds}s"

            except (aiohttp.ServerTimeoutError, aiohttp.ClientPayloadError):
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                return "TIMEOUT", round(elapsed_ms, 2), "Proxy payload or server timeout"

            except aiohttp.ClientProxyConnectionError as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                err_str = str(e).lower()
                if "407" in err_str or "auth" in err_str:
                    return "REJECTED", round(elapsed_ms, 2), f"Authentication rejected: {e}"
                last_error = f"Proxy server unreachable: {e}"
                break

            except aiohttp.ClientError as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                err_str = str(e).lower()
                if "407" in err_str or "unauthorized" in err_str or "proxy auth" in err_str:
                    return "REJECTED", round(elapsed_ms, 2), f"Authentication rejected: {e}"
                last_error = f"Network client error: {e}"
                continue

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                logger.error(f"Unexpected error verifying proxy: {e}", exc_info=True)
                last_error = f"Internal verification error: {e}"
                continue

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        return "TEMPORARY_ERROR", round(elapsed_ms, 2), last_error or "Endpoint verification failed"

    # -------------------------------------------------------------
    # Submission & Reward Pipeline
    # -------------------------------------------------------------
    @classmethod
    async def submit_and_verify_proxy(
        cls,
        telegram_user_id: int,
        username: str,
        password: str,
        is_admin: bool = False
    ) -> Dict[str, Any]:
        """
        Complete verification and reward pipeline:
        1. Check feature enabled state.
        2. Check timeout/rate-limit.
        3. Check duplicate approved fingerprint.
        4. Execute async verification.
        5. Record submission.
        6. If approved, evaluate reward milestone (every 2 approved proxies = 1 reward).
        7. Deduct stock and deliver or place in pending queue.
        Returns comprehensive result dictionary.
        """
        async with AsyncSessionLocal() as db:
            settings_obj = await cls.get_settings(db)
            if not settings_obj.enabled and not is_admin:
                return {
                    "ok": False,
                    "status": "DISABLED",
                    "message": "⚠️ Free Indo IGS is currently paused for maintenance. Please check back later."
                }

            # 1. Rate-limit check
            is_restr, rem_mins, rem_secs = await cls.check_user_timeout(db, telegram_user_id, is_admin)
            if is_restr:
                return {
                    "ok": False,
                    "status": "RESTRICTED",
                    "remaining_minutes": rem_mins,
                    "message": (
                        "🚫 <b>Submission Temporarily Restricted</b>\n\n"
                        "You have submitted too many invalid proxy credentials.\n\n"
                        f"Your submission access has been temporarily restricted for {rem_mins} minutes.\n\n"
                        "Please try again after the timeout expires.\n\n"
                        "Your previously approved proxies and reward progress are safe."
                    )
                }

            host = settings_obj.proxy_host
            if host.strip().lower() in ("pr.oxy.labs.io", "pr.oxylabs.io"):
                host = "pr.oxylabs.io"
            port = settings_obj.proxy_port
            timeout_sec = settings_obj.connection_timeout
            max_fails = settings_obj.max_failed_attempts
            timeout_mins = settings_obj.timeout_minutes

            fingerprint = cls.generate_fingerprint(host, port, username)

            # 2. Duplicate detection
            if await cls.is_proxy_already_approved(db, fingerprint):
                return {
                    "ok": False,
                    "status": "DUPLICATE",
                    "message": "⚠️ This proxy has already been verified and cannot be used again for another reward."
                }

        # 3. Connect and verify
        status, resp_time, failure_reason = await cls.verify_proxy_connection(
            host=host,
            port=port,
            username=username,
            password=password,
            timeout_seconds=timeout_sec
        )

        # 4. Handle results under transactional lock to eliminate race conditions
        async with cls._reward_lock:
            async with AsyncSessionLocal() as db:
                now_utc = datetime.now(timezone.utc)

                if status == "APPROVED":
                    # Check duplicate again under lock
                    if await cls.is_proxy_already_approved(db, fingerprint):
                        return {
                            "ok": False,
                            "status": "DUPLICATE",
                            "message": "⚠️ This proxy has already been verified and cannot be used again for another reward."
                        }

                    # Reset failure count on success
                    await cls.reset_failures(db, telegram_user_id)

                    # Encrypt password for storage at rest
                    enc_pwd = cls.encrypt_password(password)

                    # Save submission
                    submission = ProxySubmission(
                        telegram_user_id=telegram_user_id,
                        credential_fingerprint=fingerprint,
                        proxy_host=host,
                        proxy_port=port,
                        username=username,
                        password_encrypted=enc_pwd,
                        status="APPROVED",
                        response_time_ms=resp_time,
                        failure_reason=None,
                        retrieval_count=0,
                        created_at=now_utc,
                        verified_at=now_utc
                    )
                    db.add(submission)

                    # Update UserRewardProgress
                    stmt_prog = select(UserRewardProgress).where(UserRewardProgress.telegram_user_id == telegram_user_id)
                    prog = (await db.execute(stmt_prog)).scalar_one_or_none()
                    if not prog:
                        prog = UserRewardProgress(
                            telegram_user_id=telegram_user_id,
                            approved_proxy_count=1,
                            rewards_earned=0,
                            rewards_delivered=0,
                            pending_rewards=0,
                            updated_at=now_utc
                        )
                        db.add(prog)
                    else:
                        prog.approved_proxy_count += 1
                        prog.updated_at = now_utc

                    await db.flush()

                    current_approved = prog.approved_proxy_count
                    new_rewards_earned = current_approved // 2
                    is_milestone = (new_rewards_earned > prog.rewards_earned)

                    account_delivered = None
                    reward_status = None

                    if is_milestone:
                        prog.rewards_earned = new_rewards_earned

                        # Check account stock
                        available_cats = StockTxtService.get_available_categories()
                        if available_cats:
                            # Pick category with most stock to preserve diversity
                            target_cat = max(available_cats, key=lambda c: c["quantity"])
                            day_num = target_cat["day"]

                            txt_ok, _, accounts = StockTxtService.purchase_accounts(
                                day_number=day_num,
                                quantity=1
                            )
                            if txt_ok and accounts:
                                account_delivered = accounts[0]
                                prog.rewards_delivered += 1
                                reward_status = "DELIVERED"

                                tx = RewardTransaction(
                                    telegram_user_id=telegram_user_id,
                                    reward_status="DELIVERED",
                                    account_email=account_delivered.get("email"),
                                    account_raw=account_delivered.get("raw_line"),
                                    created_at=now_utc,
                                    delivered_at=now_utc
                                )
                                db.add(tx)
                            else:
                                prog.pending_rewards += 1
                                reward_status = "PENDING"
                                tx = RewardTransaction(
                                    telegram_user_id=telegram_user_id,
                                    reward_status="PENDING",
                                    account_email=None,
                                    account_raw=None,
                                    created_at=now_utc,
                                    delivered_at=None
                                )
                                db.add(tx)
                        else:
                            prog.pending_rewards += 1
                            reward_status = "PENDING"
                            tx = RewardTransaction(
                                telegram_user_id=telegram_user_id,
                                reward_status="PENDING",
                                account_email=None,
                                account_raw=None,
                                created_at=now_utc,
                                delivered_at=None
                            )
                            db.add(tx)

                    await db.commit()

                    return {
                        "ok": True,
                        "status": "APPROVED",
                        "response_time_ms": resp_time,
                        "approved_count": current_approved,
                        "is_milestone": is_milestone,
                        "reward_status": reward_status,
                        "account": account_delivered,
                        "proxy_host": host,
                        "proxy_port": port,
                    }

                elif status == "REJECTED":
                    # Record rejected submission for auditing
                    sub = ProxySubmission(
                        telegram_user_id=telegram_user_id,
                        credential_fingerprint=fingerprint,
                        proxy_host=host,
                        proxy_port=port,
                        username=username,
                        password_encrypted=None,
                        status="REJECTED",
                        response_time_ms=resp_time,
                        failure_reason=failure_reason,
                        created_at=now_utc,
                        verified_at=now_utc
                    )
                    db.add(sub)
                    await db.commit()

                    # Increment failure counter
                    timed_out, mins, total_f = await cls.record_failure(
                        db=db,
                        telegram_user_id=telegram_user_id,
                        is_admin=is_admin,
                        max_fails=max_fails,
                        timeout_minutes=timeout_mins
                    )
                    return {
                        "ok": False,
                        "status": "REJECTED",
                        "response_time_ms": resp_time,
                        "failure_reason": failure_reason,
                        "timed_out": timed_out,
                        "timeout_minutes": mins,
                        "consecutive_failures": total_f,
                        "max_fails": max_fails,
                    }

                elif status == "TIMEOUT":
                    sub = ProxySubmission(
                        telegram_user_id=telegram_user_id,
                        credential_fingerprint=fingerprint,
                        proxy_host=host,
                        proxy_port=port,
                        username=username,
                        password_encrypted=None,
                        status="TIMEOUT",
                        response_time_ms=resp_time,
                        failure_reason=failure_reason,
                        created_at=now_utc,
                        verified_at=now_utc
                    )
                    db.add(sub)
                    await db.commit()

                    # Timeouts do not count as invalid credential attempts per spec
                    return {
                        "ok": False,
                        "status": "TIMEOUT",
                        "response_time_ms": resp_time,
                        "failure_reason": failure_reason,
                    }

                else:  # TEMPORARY_ERROR
                    sub = ProxySubmission(
                        telegram_user_id=telegram_user_id,
                        credential_fingerprint=fingerprint,
                        proxy_host=host,
                        proxy_port=port,
                        username=username,
                        password_encrypted=None,
                        status="TEMPORARY_ERROR",
                        response_time_ms=resp_time,
                        failure_reason=failure_reason,
                        created_at=now_utc,
                        verified_at=now_utc
                    )
                    db.add(sub)
                    await db.commit()

                    # Infrastructure/network errors do not count as failed attempts
                    return {
                        "ok": False,
                        "status": "TEMPORARY_ERROR",
                        "response_time_ms": resp_time,
                        "failure_reason": failure_reason,
                    }

    # -------------------------------------------------------------
    # Progress & History Inquiries
    # -------------------------------------------------------------
    @classmethod
    async def get_user_progress(cls, db: AsyncSession, telegram_user_id: int) -> Dict[str, int]:
        """Fetch current reward progress for user."""
        stmt = select(UserRewardProgress).where(UserRewardProgress.telegram_user_id == telegram_user_id)
        prog = (await db.execute(stmt)).scalar_one_or_none()
        if not prog:
            return {
                "approved_proxy_count": 0,
                "current_cycle_approved": 0,
                "remaining_for_reward": 2,
                "rewards_earned": 0,
                "rewards_delivered": 0,
                "pending_rewards": 0,
            }

        cycle_approved = prog.approved_proxy_count % 2
        remaining = 2 - cycle_approved
        return {
            "approved_proxy_count": prog.approved_proxy_count,
            "current_cycle_approved": cycle_approved,
            "remaining_for_reward": remaining,
            "rewards_earned": prog.rewards_earned,
            "rewards_delivered": prog.rewards_delivered,
            "pending_rewards": prog.pending_rewards,
        }

    # -------------------------------------------------------------
    # Pending Reward Automatic Delivery
    # -------------------------------------------------------------
    @classmethod
    async def deliver_pending_rewards(cls, bot: Any) -> int:
        """
        Process and automatically deliver all pending rewards when stock becomes available.
        Can be called by cron, after admin stock import, or via admin panel button.
        Returns: total count of delivered rewards.
        """
        delivered_count = 0
        async with cls._reward_lock:
            async with AsyncSessionLocal() as db:
                stmt = (
                    select(RewardTransaction)
                    .where(RewardTransaction.reward_status == "PENDING")
                    .order_by(RewardTransaction.created_at.asc())
                )
                pending_txs = (await db.execute(stmt)).scalars().all()
                if not pending_txs:
                    return 0

                for tx in pending_txs:
                    available_cats = StockTxtService.get_available_categories()
                    if not available_cats:
                        break

                    target_cat = max(available_cats, key=lambda c: c["quantity"])
                    day_num = target_cat["day"]

                    txt_ok, _, accounts = StockTxtService.purchase_accounts(day_number=day_num, quantity=1)
                    if not txt_ok or not accounts:
                        break

                    acc = accounts[0]
                    now_utc = datetime.now(timezone.utc)

                    tx.reward_status = "DELIVERED"
                    tx.account_email = acc.get("email")
                    tx.account_raw = acc.get("raw_line")
                    tx.delivered_at = now_utc

                    # Update progress record
                    stmt_prog = select(UserRewardProgress).where(UserRewardProgress.telegram_user_id == tx.telegram_user_id)
                    prog = (await db.execute(stmt_prog)).scalar_one_or_none()
                    if prog:
                        prog.rewards_delivered += 1
                        prog.pending_rewards = max(0, prog.pending_rewards - 1)
                        prog.updated_at = now_utc

                    await db.commit()
                    delivered_count += 1

                    # Send notification to user
                    if bot:
                        try:
                            msg_text = (
                                "🎉 <b>Congratulations!</b>\n\n"
                                "Your pending Free Indo IGS account has just been delivered!\n\n"
                                f"📧 Email: <code>{html.escape(acc.get('email', ''))}</code>\n"
                                f"🔑 Password: <code>{html.escape(acc.get('password', ''))}</code>\n"
                            )
                            if acc.get("username"):
                                msg_text += f"🫟 Username: <code>{html.escape(acc.get('username'))}</code>\n"
                            if acc.get("cookie"):
                                msg_text += f"🍪 Cookie: <code>{html.escape(acc.get('cookie'))}</code>\n"

                            msg_text += "\nThank you for participating! ❤️"
                            await bot.send_message(chat_id=tx.telegram_user_id, text=msg_text, parse_mode="HTML")
                        except Exception as e:
                            logger.error(f"Failed to send pending delivery notification to {tx.telegram_user_id}: {e}")

        return delivered_count

    # -------------------------------------------------------------
    # Additional Feature: Stored Proxies Retrieval & Statistics
    # -------------------------------------------------------------
    @classmethod
    async def get_stored_proxies(
        cls,
        db: AsyncSession,
        limit: Optional[int] = None,
        offset: int = 0,
        status_filter: str = "APPROVED"
    ) -> List[Dict[str, Any]]:
        """
        Retrieve approved stored proxies for administrative export.
        Read-only retrieval; passwords decrypted in-memory.
        """
        stmt = (
            select(ProxySubmission)
            .where(ProxySubmission.status == status_filter)
            .order_by(ProxySubmission.created_at.desc())
            .offset(offset)
        )
        if limit:
            stmt = stmt.limit(limit)

        records = (await db.execute(stmt)).scalars().all()
        results = []
        submission_ids = []

        for sub in records:
            submission_ids.append(sub.id)
            decrypted_pass = cls.decrypt_password(sub.password_encrypted or "")
            formatted = f"{sub.proxy_host}:{sub.proxy_port}:{sub.username}:{decrypted_pass}"
            results.append({
                "id": sub.id,
                "server": sub.proxy_host,
                "port": sub.proxy_port,
                "username": sub.username,
                "password": decrypted_pass,
                "owner_telegram_id": sub.telegram_user_id,
                "submission_date": sub.created_at,
                "retrieval_count": sub.retrieval_count + 1,
                "formatted": formatted,
            })

        # Atomically increment retrieval count
        if submission_ids:
            await db.execute(
                update(ProxySubmission)
                .where(ProxySubmission.id.in_(submission_ids))
                .values(retrieval_count=ProxySubmission.retrieval_count + 1)
            )
            await db.commit()

        return results

    @classmethod
    async def get_proxy_statistics(cls, db: AsyncSession) -> Dict[str, int]:
        """Fetch live counts for administrative dashboard."""
        total_stmt = select(func.count(ProxySubmission.id))
        total_submissions = (await db.execute(total_stmt)).scalar() or 0

        approved_stmt = select(func.count(ProxySubmission.id)).where(ProxySubmission.status == "APPROVED")
        approved_count = (await db.execute(approved_stmt)).scalar() or 0

        rejected_stmt = select(func.count(ProxySubmission.id)).where(ProxySubmission.status == "REJECTED")
        rejected_count = (await db.execute(rejected_stmt)).scalar() or 0

        retrieved_stmt = (
            select(func.count(ProxySubmission.id))
            .where(ProxySubmission.status == "APPROVED", ProxySubmission.retrieval_count > 0)
        )
        retrieved_count = (await db.execute(retrieved_stmt)).scalar() or 0

        available_count = approved_count - retrieved_count

        rewards_stmt = select(func.count(RewardTransaction.id)).where(RewardTransaction.reward_status == "DELIVERED")
        total_rewards = (await db.execute(rewards_stmt)).scalar() or 0

        pending_stmt = select(func.count(RewardTransaction.id)).where(RewardTransaction.reward_status == "PENDING")
        total_pending = (await db.execute(pending_stmt)).scalar() or 0

        return {
            "total_submissions": total_submissions,
            "total_stored": approved_count,
            "available_proxies": max(0, available_count),
            "retrieved_proxies": retrieved_count,
            "rejected_proxies": rejected_count,
            "total_rewards_delivered": total_rewards,
            "pending_rewards": total_pending,
        }

    # -------------------------------------------------------------
    # Administrative Submission Logs Search & Pagination
    # -------------------------------------------------------------
    @classmethod
    async def query_submissions(
        cls,
        db: AsyncSession,
        status_filter: Optional[str] = None,
        search_user_id: Optional[int] = None,
        limit: int = 15,
        offset: int = 0
    ) -> Tuple[List[ProxySubmission], int]:
        """Query submission history with status filter and user search."""
        query = select(ProxySubmission)
        count_query = select(func.count(ProxySubmission.id))

        filters = []
        if status_filter and status_filter.upper() != "ALL":
            filters.append(ProxySubmission.status == status_filter.upper())
        if search_user_id:
            filters.append(ProxySubmission.telegram_user_id == search_user_id)

        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))

        total = (await db.execute(count_query)).scalar() or 0
        records = (
            (await db.execute(query.order_by(desc(ProxySubmission.created_at)).offset(offset).limit(limit)))
            .scalars()
            .all()
        )
        return list(records), total
