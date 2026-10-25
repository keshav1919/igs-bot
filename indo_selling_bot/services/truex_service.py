"""
TrueXGateway Integration Service.
Handles:
- Dynamic UPI QR generation with unique decimal amounts for collision-free auto-detection
- Fetching QR images directly via TrueXGateway /api/generate-qr.php endpoint
- Real-time payment verification polling via /api/payment-status.php
- Atomic wallet crediting and immutable ledger recording
- In-place QR photo replacement with Payment Successful graphic (image 2) and custom bold text
- Success sticker delivery (ExTechie doge sticker) directly below the updated message
- Detailed payment notification sent to administrators
"""
import io
import asyncio
import logging
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

import httpx
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaPhoto, Message
import qrcode
from PIL import Image, ImageOps

from indo_selling_bot.config import settings
from indo_selling_bot.database import AsyncSessionLocal
from indo_selling_bot.models.wallet import DepositRequest, Wallet, WalletTransaction
from indo_selling_bot.models.audit import AuditLog

logger = logging.getLogger("truex_gateway")


def format_success_caption(amount: float, order_id: str, paid_at_dt: Optional[datetime] = None) -> str:
    """
    Format exact payment success notification message matching user requirements:
    
     𝐏𝐀𝐘𝐌𝐄𝐍𝐓 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋 ✅ 
    ———————————————-
    💰 Amount:  ₹1.02
    🆔 Order ID: TG_PAY_MVNVLVM5_768
    ————————————————

    🕒 Paid At: 25 Oct, 07:11 PM
    """
    if not paid_at_dt:
        try:
            paid_at_dt = datetime.now(ZoneInfo(settings.BUSINESS_TIMEZONE))
        except Exception:
            paid_at_dt = datetime.now(timezone.utc)

    paid_at_str = paid_at_dt.strftime("%d %b, %I:%M %p")
    return (
        " 𝐏𝐀𝐘𝐌𝐄𝐍𝐓 𝐒𝐔𝐂𝐂𝐄𝐒𝐒𝐅𝐔𝐋 ✅ \n"
        "———————————————-\n"
        f"💰 Amount:  ₹{amount:.2f}\n"
        f"🆔 Order ID: {order_id}\n"
        "————————————————\n\n"
        f"🕒 Paid At: {paid_at_str}"
    )


class TrueXGatewayService:
    @staticmethod
    async def generate_order_ref(db: AsyncSession) -> str:
        """
        Generate collision-free order ID matching TG_PAY_MVNVLVM5_768 pattern.
        """
        chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(10):
            code = "".join(secrets.choice(chars) for _ in range(8))
            num = secrets.randbelow(900) + 100
            ref = f"TG_PAY_{code}_{num}"
            existing = (
                await db.execute(
                    select(DepositRequest).where(DepositRequest.reference == ref)
                )
            ).scalar_one_or_none()
            if not existing:
                return ref
        return f"TG_PAY_{secrets.token_hex(4).upper()}_{secrets.randbelow(900)+100}"

    @staticmethod
    async def calculate_unique_amount(db: AsyncSession, base_amount: float) -> float:
        """
        Generate unique decimal amount (e.g. 50.18, 1.02) to ensure collision-free auto-detection.
        Scans currently pending deposits created in the last 20 minutes to avoid colliding amounts.
        """
        base = int(base_amount)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
        stmt = select(DepositRequest.amount).where(
            DepositRequest.status.in_(["PENDING_PAYMENT", "PENDING"]),
            DepositRequest.created_at >= cutoff
        )
        existing_amounts = set((await db.execute(stmt)).scalars().all())

        # If user explicitly passed a non-round decimal (e.g. 1.02) and it's not currently taken:
        if round(base_amount, 2) != float(base) and round(base_amount, 2) not in existing_amounts:
            return round(base_amount, 2)

        # Pick random decimal cents between 0.01 and 0.99 not currently in flight
        for _ in range(100):
            cents = secrets.randbelow(99) + 1
            cand = round(base + (cents / 100.0), 2)
            if cand not in existing_amounts:
                return cand

        # Fallback
        return round(base + ((secrets.randbelow(99) + 1) / 100.0), 2)

    @staticmethod
    async def create_order(amount: float, order_ref: str, description: str = "") -> dict:
        """
        Call TrueXGateway /api/create-order.php to initiate dynamic UPI payment.
        """
        url = f"{settings.TRUEXGATEWAY_BASE_URL.rstrip('/')}/create-order.php"
        headers = {
            "X-API-Key": settings.TRUEXGATEWAY_API_KEY,
            "Content-Type": "application/json"
        }
        payload = {
            "amount": f"{amount:.2f}",
            "orderRef": order_ref,
            "description": description or f"Wallet Topup {order_ref}"
        }
        logger.info(f"Creating TrueXGateway order: ref={order_ref}, amount={amount:.2f}")
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code != 200:
                logger.error(f"TrueXGateway create-order failed HTTP {resp.status_code}: {resp.text}")
                raise RuntimeError(f"TrueXGateway HTTP error {resp.status_code}: {resp.text}")
            data = resp.json()
            if not data.get("success"):
                err_msg = data.get("message") or "Failed to create TrueXGateway order"
                logger.error(f"TrueXGateway create-order error: {err_msg}")
                raise ValueError(err_msg)
            return data

    @staticmethod
    def generate_qr_bytes(upi_intent: str, padding_percent: float = 0.15) -> bytes:
        """
        Generate dynamic QR image locally from UPI intent in < 25ms
        with 15% padding on all sides.
        """
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=2,
        )
        qr.add_data(upi_intent)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

        if padding_percent > 0:
            pad = int(img.width * padding_percent)
            img = ImageOps.expand(img, border=pad, fill="white")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    async def fetch_qr_image_bytes(
        txg_order_id: str,
        qr_url_fallback: Optional[str] = None,
        upi_intent: Optional[str] = None
    ) -> bytes:
        """
        Fetch / generate high-resolution QR image with 15% padding from all sides.
        Uses instant local generation from UPI intent (< 25ms) for maximum speed.
        """
        # Fast path: instant local generation (no network latency)
        if upi_intent:
            try:
                return TrueXGatewayService.generate_qr_bytes(upi_intent, padding_percent=0.15)
            except Exception as e:
                logger.warning(f"Local QR generation failed, falling back to network: {e}")

        # Fallback 1: TrueXGateway generate-qr.php endpoint
        url = f"{settings.TRUEXGATEWAY_BASE_URL.rstrip('/')}/generate-qr.php?orderId={txg_order_id}&public=1"
        headers = {"X-API-Key": settings.TRUEXGATEWAY_API_KEY}
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200 and len(resp.content) > 100:
                    img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                    pad = int(img.width * 0.15)
                    padded = ImageOps.expand(img, border=pad, fill="white")
                    buf = io.BytesIO()
                    padded.save(buf, format="PNG")
                    return buf.getvalue()
        except Exception as e:
            logger.warning(f"generate-qr.php request error for {txg_order_id}: {e}")

        # Fallback 2: QuickChart qrUrl provided in create-order response
        if qr_url_fallback:
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(qr_url_fallback)
                    if resp.status_code == 200 and len(resp.content) > 100:
                        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                        pad = int(img.width * 0.15)
                        padded = ImageOps.expand(img, border=pad, fill="white")
                        buf = io.BytesIO()
                        padded.save(buf, format="PNG")
                        return buf.getvalue()
            except Exception as e_fb:
                logger.warning(f"qrUrl fallback failed: {e_fb}")

        raise RuntimeError("Unable to retrieve or generate QR code image.")

    @staticmethod
    async def check_payment_status(txg_order_id: str) -> dict:
        """
        Poll /api/payment-status.php to check real-time bank verification status.
        """
        url = f"{settings.TRUEXGATEWAY_BASE_URL.rstrip('/')}/payment-status.php?orderId={txg_order_id}&public=1"
        headers = {"X-API-Key": settings.TRUEXGATEWAY_API_KEY}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(f"Payment status check HTTP {resp.status_code}: {resp.text}")
                return {"success": False, "status": "unknown"}
            return resp.json()

    @staticmethod
    async def process_successful_payment(
        bot: Bot,
        order_ref: str,
        utr: Optional[str] = None,
        txg_order_id: Optional[str] = None,
        db: Optional[AsyncSession] = None
    ) -> bool:
        """
        Atomically process payment confirmation:
        1. Atomic wallet crediting in database (idempotent, once only)
        2. Replace the existing QR message with image 2 and exact success text (no new message)
        3. Send the ExTechie sticker directly below the updated message
        4. Send payment details to administrator
        """
        paid_at_dt = datetime.now(ZoneInfo(settings.BUSINESS_TIMEZONE))
        paid_at_str = paid_at_dt.strftime("%d %b, %I:%M %p")

        async def _do_process(session: AsyncSession):
            stmt = select(DepositRequest).where(DepositRequest.reference == order_ref)
            deposit = (await session.execute(stmt)).scalar_one_or_none()
            if not deposit:
                logger.error(f"Cannot process success: deposit {order_ref} not found.")
                return False, None, None

            if deposit.status in ("APPROVED", "SUCCESS"):
                logger.info(f"Deposit {order_ref} already processed ({deposit.status}). Skipping.")
                return False, deposit, None

            # Retrieve customer wallet
            stmt_w = select(Wallet).where(Wallet.telegram_user_id == deposit.telegram_user_id)
            wallet = (await session.execute(stmt_w)).scalar_one_or_none()
            if not wallet:
                wallet = Wallet(
                    telegram_user_id=deposit.telegram_user_id,
                    balance=0.0,
                    currency=deposit.currency,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(wallet)
                await session.flush()

            new_balance = float(wallet.balance) + float(deposit.amount)
            wallet.balance = new_balance
            wallet.updated_at = datetime.now(timezone.utc)

            deposit.status = "APPROVED"
            deposit.processed_at = datetime.now(timezone.utc)
            if utr:
                deposit.utr_number = utr
            if txg_order_id:
                deposit.txg_order_id = txg_order_id

            # Immutable ledger entry
            tx = WalletTransaction(
                wallet_id=wallet.id,
                telegram_user_id=deposit.telegram_user_id,
                tx_type="DEPOSIT",
                amount=deposit.amount,
                balance_after=new_balance,
                reference=deposit.reference,
                description=f"Auto UPI Deposit via TrueXGateway (UTR: {utr or 'auto'})",
                created_at=datetime.now(timezone.utc)
            )
            session.add(tx)

            # Audit log
            audit = AuditLog(
                action="DEPOSIT_AUTO_APPROVED",
                performed_by=f"TrueXGateway:{deposit.txg_order_id or 'api'}",
                details=f"Deposit {deposit.reference} auto-approved. Amount: {deposit.amount}, UTR: {utr or 'auto'}",
                created_at=datetime.now(timezone.utc)
            )
            session.add(audit)
            await session.commit()
            return True, deposit, new_balance

        if db is not None:
            processed, deposit, new_balance = await _do_process(db)
        else:
            async with AsyncSessionLocal() as session:
                processed, deposit, new_balance = await _do_process(session)

        if not processed or not deposit:
            return False

        logger.info(
            f"✅ Payment auto-approved: {order_ref} | Amount: ₹{deposit.amount:.2f} | "
            f"User: {deposit.telegram_user_id} | New Balance: ₹{new_balance:.2f}"
        )

        # ----------------------------------------------------
        # 1. Update the original QR message with Image 2 + text (in-place)
        # ----------------------------------------------------
        success_caption = format_success_caption(deposit.amount, deposit.reference, paid_at_dt)
        success_img = settings.SUCCESS_IMAGE_PATH
        if not success_img.exists():
            success_img = Path("assets/payment_success.png")

        edited_ok = False
        if deposit.chat_id and deposit.message_id and success_img.exists():
            try:
                media = InputMediaPhoto(
                    media=FSInputFile(str(success_img)),
                    caption=success_caption
                )
                await bot.edit_message_media(
                    chat_id=deposit.chat_id,
                    message_id=deposit.message_id,
                    media=media,
                    reply_markup=None
                )
                edited_ok = True
                logger.info(f"Updated QR message {deposit.message_id} with success image.")
            except Exception as edit_err:
                logger.warning(f"Could not edit QR message media: {edit_err}")

        # Fallback ONLY if original message was deleted or edit failed
        if not edited_ok and deposit.chat_id:
            try:
                if success_img.exists():
                    await bot.send_photo(
                        chat_id=deposit.chat_id,
                        photo=FSInputFile(str(success_img)),
                        caption=success_caption
                    )
                else:
                    await bot.send_message(
                        chat_id=deposit.chat_id,
                        text=success_caption
                    )
            except Exception as send_err:
                logger.error(f"Fallback send failed: {send_err}")

        # ----------------------------------------------------
        # 2. Send the Doge Sticker below the updated message
        # ----------------------------------------------------
        if deposit.chat_id:
            sticker_sent = False
            sticker_id = settings.SUCCESS_STICKER_FILE_ID or "CAACAgUAAxkBAAIL6WreHy0uBxynl7ZRxMMpFc4Q_mmTAAIVCwAC3ReQVvwjxMtoqXwWPQQ"
            try:
                await bot.send_sticker(
                    chat_id=deposit.chat_id,
                    sticker=sticker_id
                )
                sticker_sent = True
            except Exception as s_err:
                logger.warning(f"Failed sending sticker by file_id: {s_err}")

            # Option B: Send local sticker webp file if file_id fails
            if not sticker_sent:
                sticker_path = settings.SUCCESS_STICKER_PATH
                if not sticker_path.exists():
                    sticker_path = Path("assets/payment_success_sticker.webp")

                if sticker_path.exists():
                    try:
                        sent_msg = await bot.send_sticker(
                            chat_id=deposit.chat_id,
                            sticker=FSInputFile(str(sticker_path))
                        )
                        if sent_msg and sent_msg.sticker:
                            settings.SUCCESS_STICKER_FILE_ID = sent_msg.sticker.file_id
                    except Exception as s_err:
                        logger.error(f"Failed sending sticker file: {s_err}")

        # ----------------------------------------------------
        # 3. Send payment details to administrator
        # ----------------------------------------------------
        admin_text = (
            "🔔 <b>PAYMENT AUTO-VERIFIED & CREDITED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Order ID:</b> <code>{deposit.reference}</code>\n"
            f"🌐 <b>Gateway Order ID:</b> <code>{deposit.txg_order_id or '-'}</code>\n"
            f"👤 <b>Customer ID:</b> <code>{deposit.telegram_user_id}</code>\n"
            f"💰 <b>Amount Credited:</b> <code>₹{deposit.amount:,.2f}</code>\n"
            f"🧾 <b>UTR Number:</b> <code>{utr or 'Auto-matched'}</code>\n"
            f"🕒 <b>Paid At:</b> <code>{paid_at_str}</code>\n"
            f"💳 <b>Updated Balance:</b> <code>₹{new_balance:,.2f}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "⚡ <i>Auto-processed via TrueXGateway</i>"
        )
        for admin_id in settings.admin_ids:
            try:
                await bot.send_message(chat_id=admin_id, text=admin_text, parse_mode="HTML")
            except Exception as adm_err:
                logger.warning(f"Admin notification failed for {admin_id}: {adm_err}")

        return True


class TrueXPollingManager:
    """
    Manages non-blocking background polling tasks for pending TrueXGateway orders.
    Checks status every 3-4 seconds as recommended by TrueXGateway documentation.
    """
    def __init__(self):
        self._active_tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start_polling(self, bot: Bot, txg_order_id: str, order_ref: str, timeout_seconds: int = 900):
        async with self._lock:
            if txg_order_id in self._active_tasks:
                self._active_tasks[txg_order_id].cancel()

            task = asyncio.create_task(
                self._poll_loop(bot, txg_order_id, order_ref, timeout_seconds)
            )
            self._active_tasks[txg_order_id] = task
            logger.info(f"Registered polling task for order {order_ref} ({txg_order_id})")

    async def stop_polling(self, txg_order_id: str):
        async with self._lock:
            if txg_order_id in self._active_tasks:
                self._active_tasks[txg_order_id].cancel()
                self._active_tasks.pop(txg_order_id, None)
                logger.info(f"Stopped polling task for {txg_order_id}")

    async def _poll_loop(self, bot: Bot, txg_order_id: str, order_ref: str, timeout_seconds: int):
        start_time = asyncio.get_event_loop().time()
        poll_interval = 3.5
        try:
            while True:
                elapsed = asyncio.get_event_loop().time() - start_time
                if elapsed > timeout_seconds:
                    logger.info(f"Polling timeout reached for {txg_order_id} ({order_ref})")
                    async with AsyncSessionLocal() as db:
                        stmt = select(DepositRequest).where(DepositRequest.reference == order_ref)
                        dep = (await db.execute(stmt)).scalar_one_or_none()
                        if dep and dep.status in ("PENDING", "PENDING_PAYMENT"):
                            dep.status = "EXPIRED"
                            await db.commit()
                    break

                try:
                    data = await TrueXGatewayService.check_payment_status(txg_order_id)
                    status = (data.get("status") or "").lower()
                    verified = data.get("verified") is True
                    if status == "success" or verified:
                        utr = data.get("utr") or ""
                        logger.info(f"Auto-detection detected success for {txg_order_id} (ref={order_ref}, utr={utr})")
                        await TrueXGatewayService.process_successful_payment(
                            bot=bot,
                            order_ref=order_ref,
                            utr=utr,
                            txg_order_id=txg_order_id
                        )
                        break
                    elif status in ("failed", "cancelled", "rejected"):
                        logger.info(f"Payment marked {status} on gateway for {txg_order_id}")
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.warning(f"Error checking payment status for {txg_order_id}: {e}")

                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            logger.info(f"Polling task cancelled for {txg_order_id}")
        finally:
            async with self._lock:
                self._active_tasks.pop(txg_order_id, None)

    async def resume_all_pending(self, bot: Bot):
        """
        On bot startup, scan the database for active pending payments created in the last 20 minutes
        and resume polling tasks for each.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
        try:
            async with AsyncSessionLocal() as db:
                stmt = select(DepositRequest).where(
                    DepositRequest.status.in_(["PENDING_PAYMENT", "PENDING"]),
                    DepositRequest.txg_order_id.isnot(None),
                    DepositRequest.created_at >= cutoff
                )
                pendings = (await db.execute(stmt)).scalars().all()
                for p in pendings:
                    if p.txg_order_id and p.reference:
                        logger.info(f"Resuming polling for pending deposit {p.reference} ({p.txg_order_id})")
                        await self.start_polling(bot, p.txg_order_id, p.reference)
        except Exception as e:
            logger.error(f"Error resuming pending polls on startup: {e}")


polling_manager = TrueXPollingManager()
