"""
Wallet and Deposit Management Service.
Handles customer wallets, atomic balance changes, immutable transaction ledgers,
and administrator deposit approval workflows.
"""
import secrets
from datetime import datetime, timezone
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from indo_selling_bot.models.wallet import Customer, Wallet, WalletTransaction, DepositRequest
from indo_selling_bot.models.audit import AuditLog
from indo_selling_bot.config import settings

class WalletService:
    @staticmethod
    async def get_or_create_customer(
        db: AsyncSession,
        telegram_id: int,
        username: str | None = None,
        first_name: str | None = None
    ) -> Customer:
        """Fetch existing customer or register a new one."""
        stmt = select(Customer).where(Customer.telegram_id == telegram_id)
        customer = (await db.execute(stmt)).scalar_one_or_none()
        if not customer:
            customer = Customer(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                created_at=datetime.now(timezone.utc),
                last_active_at=datetime.now(timezone.utc)
            )
            db.add(customer)
            await db.flush()
        else:
            # Update latest handle / active timestamp
            if username:
                customer.username = username
            if first_name:
                customer.first_name = first_name
            customer.last_active_at = datetime.now(timezone.utc)
            await db.flush()
        return customer

    @staticmethod
    async def get_or_create_wallet(
        db: AsyncSession,
        telegram_user_id: int,
        currency: str = "INR",
        username: str | None = None,
        first_name: str | None = None
    ) -> Wallet:
        """Retrieve existing customer wallet or initialize one with 0 balance."""
        await WalletService.get_or_create_customer(db, telegram_user_id, username, first_name)
        stmt = select(Wallet).where(Wallet.telegram_user_id == telegram_user_id)
        wallet = (await db.execute(stmt)).scalar_one_or_none()
        if not wallet:
            wallet = Wallet(
                telegram_user_id=telegram_user_id,
                balance=0,
                currency=currency,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            db.add(wallet)
            await db.flush()
        return wallet

    @staticmethod
    async def get_balance(db: AsyncSession, telegram_user_id: int) -> float:
        """Get current confirmed wallet balance."""
        wallet = await WalletService.get_or_create_wallet(db, telegram_user_id)
        return float(wallet.balance)

    @staticmethod
    async def create_deposit_request(
        db: AsyncSession,
        telegram_user_id: int,
        amount: float,
        reference: str | None = None,
        txg_order_id: str | None = None,
        message_id: int | None = None,
        chat_id: int | None = None,
        currency: str | None = None,
        utr_number: str | None = None,
        screenshot_file_id: str | None = None,
        username: str | None = None,
        first_name: str | None = None,
        initial_status: str = "PENDING_PAYMENT"
    ) -> DepositRequest:
        """
        Create a unique pending deposit request.
        """
        is_admin = settings.is_admin(telegram_user_id)
        min_dep = 1.0 if is_admin else float(settings.MIN_DEPOSIT_AMOUNT)
        if amount <= 0:
            raise ValueError("Deposit amount must be greater than ₹0.")
        if not is_admin and amount < min_dep:
            raise ValueError(f"Minimum Add Fund Amount is ₹{int(min_dep)}. Please enter ₹{int(min_dep)} or more.")

        await WalletService.get_or_create_customer(db, telegram_user_id, username, first_name)
        curr = currency or settings.DEFAULT_CURRENCY

        if not reference:
            ref = str(secrets.randbelow(9000000000) + 1000000000)
        else:
            ref = reference

        deposit = DepositRequest(
            reference=ref,
            telegram_user_id=telegram_user_id,
            amount=amount,
            currency=curr,
            status=initial_status,
            utr_number=utr_number.strip() if utr_number else None,
            screenshot_file_id=screenshot_file_id,
            txg_order_id=txg_order_id,
            message_id=message_id,
            chat_id=chat_id,
            created_at=datetime.now(timezone.utc)
        )
        db.add(deposit)
        await db.flush()
        return deposit

    @staticmethod
    async def attach_screenshot(
        db: AsyncSession,
        deposit_ref: str,
        screenshot_file_id: str
    ) -> tuple[bool, str, DepositRequest | None]:
        """Attach customer payment screenshot and update status to PENDING_REVIEW."""
        deposit = await WalletService.get_deposit_by_ref(db, deposit_ref)
        if not deposit:
            return False, f"Payment request {deposit_ref} not found.", None

        if deposit.status in ("APPROVED", "REJECTED"):
            return False, f"Payment request {deposit_ref} was already processed ({deposit.status}).", deposit

        if deposit.status == "PENDING_REVIEW":
            return False, "Your payment screenshot has already been submitted and is currently under review by the administrator.", deposit

        deposit.screenshot_file_id = screenshot_file_id
        deposit.status = "PENDING_REVIEW"
        await db.commit()
        return True, "Payment screenshot attached successfully.", deposit

    @staticmethod
    async def get_latest_pending_deposit(
        db: AsyncSession,
        telegram_user_id: int
    ) -> DepositRequest | None:
        """Find the latest active pending payment request for user."""
        stmt = (
            select(DepositRequest)
            .where(
                DepositRequest.telegram_user_id == telegram_user_id,
                DepositRequest.status.in_(["PENDING_PAYMENT", "PENDING", "PENDING_REVIEW"])
            )
            .order_by(desc(DepositRequest.created_at))
            .limit(1)
        )
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_customer_deposit_requests(
        db: AsyncSession,
        telegram_user_id: int,
        limit: int = 10
    ) -> list[DepositRequest]:
        """Fetch customer's recent deposit requests."""
        stmt = (
            select(DepositRequest)
            .where(DepositRequest.telegram_user_id == telegram_user_id)
            .order_by(desc(DepositRequest.created_at))
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get_deposit_by_ref(db: AsyncSession, reference: str) -> DepositRequest | None:
        """Look up deposit request by reference code."""
        stmt = select(DepositRequest).where(DepositRequest.reference == reference.strip().upper())
        return (await db.execute(stmt)).scalar_one_or_none()

    @staticmethod
    async def get_pending_deposits(db: AsyncSession, limit: int = 20) -> list[DepositRequest]:
        """List all pending deposits awaiting admin verification."""
        stmt = (
            select(DepositRequest)
            .where(DepositRequest.status.in_(["PENDING", "PENDING_PAYMENT", "PENDING_REVIEW"]))
            .order_by(desc(DepositRequest.created_at))
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def approve_deposit(
        db: AsyncSession,
        deposit_ref: str,
        admin_id: int,
        admin_notes: str | None = None
    ) -> tuple[bool, str, DepositRequest | None]:
        """
        Approve deposit request, atomically credit customer wallet,
        and write immutable ledger entry. Idempotent on repeated calls.
        Enforces server-side minimum deposit of ₹30.
        """
        deposit = await WalletService.get_deposit_by_ref(db, deposit_ref)
        if not deposit:
            return False, "Deposit request not found.", None

        if deposit.status not in ("PENDING", "PENDING_PAYMENT", "PENDING_REVIEW"):
            return False, f"Deposit {deposit_ref} was already processed (status: {deposit.status}).", deposit

        is_admin = settings.is_admin(deposit.telegram_user_id)
        min_dep = 1.0 if is_admin else float(settings.MIN_DEPOSIT_AMOUNT)
        if not is_admin and deposit.amount < min_dep:
            return False, f"Deposit amount ₹{deposit.amount} is below minimum allowed deposit ₹{int(min_dep)}.", deposit

        wallet = await WalletService.get_or_create_wallet(db, deposit.telegram_user_id, deposit.currency)

        # Atomic credit
        new_balance = wallet.balance + deposit.amount
        wallet.balance = new_balance
        wallet.updated_at = datetime.now(timezone.utc)

        # Mark deposit approved
        deposit.status = "APPROVED"
        deposit.admin_id = admin_id
        deposit.admin_notes = admin_notes
        deposit.processed_at = datetime.now(timezone.utc)

        # Record immutable ledger transaction
        tx = WalletTransaction(
            wallet_id=wallet.id,
            telegram_user_id=deposit.telegram_user_id,
            tx_type="DEPOSIT",
            amount=deposit.amount,
            balance_after=new_balance,
            reference=deposit.reference,
            admin_id=admin_id,
            description=admin_notes or f"Deposit verified by admin {admin_id}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(tx)

        # Audit log
        audit = AuditLog(
            action="APPROVE_DEPOSIT",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=f"Approved {deposit.reference} for user {deposit.telegram_user_id}, amount: {deposit.amount} {deposit.currency}"
        )
        db.add(audit)

        await db.commit()
        return True, f"Deposit {deposit_ref} approved and {deposit.amount:,.0f} {deposit.currency} credited.", deposit

    @staticmethod
    async def reject_deposit(
        db: AsyncSession,
        deposit_ref: str,
        admin_id: int,
        reason: str | None = None
    ) -> tuple[bool, str, DepositRequest | None]:
        """Reject deposit request without modifying wallet balance."""
        deposit = await WalletService.get_deposit_by_ref(db, deposit_ref)
        if not deposit:
            return False, "Deposit request not found.", None

        if deposit.status not in ("PENDING", "PENDING_PAYMENT", "PENDING_REVIEW"):
            return False, f"Deposit {deposit_ref} was already processed (status: {deposit.status}).", deposit

        deposit.status = "REJECTED"
        deposit.admin_id = admin_id
        deposit.admin_notes = reason or "Payment could not be verified."
        deposit.processed_at = datetime.now(timezone.utc)

        audit = AuditLog(
            action="REJECT_DEPOSIT",
            performed_by=f"TELEGRAM_ADMIN:{admin_id}",
            details=f"Rejected {deposit.reference} for user {deposit.telegram_user_id}: {reason}"
        )
        db.add(audit)

        await db.commit()
        return True, f"Deposit {deposit_ref} rejected.", deposit

    @staticmethod
    async def deduct_for_purchase(
        db: AsyncSession,
        telegram_user_id: int,
        amount: int,
        order_code: str,
        item_title: str
    ) -> tuple[bool, str, int]:
        """
        Atomically validate and deduct balance for order purchase.
        Returns: (success: bool, message: str, remaining_balance: int)
        """
        wallet = await WalletService.get_or_create_wallet(db, telegram_user_id)
        if wallet.balance < amount:
            return False, f"Insufficient balance. Required: {amount:,.0f}, Available: {wallet.balance:,.0f}.", wallet.balance

        new_balance = wallet.balance - amount
        wallet.balance = new_balance
        wallet.updated_at = datetime.now(timezone.utc)

        tx = WalletTransaction(
            wallet_id=wallet.id,
            telegram_user_id=telegram_user_id,
            tx_type="PURCHASE",
            amount=-amount,
            balance_after=new_balance,
            reference=order_code,
            description=f"Purchase of {item_title}",
            created_at=datetime.now(timezone.utc)
        )
        db.add(tx)
        await db.flush()
        return True, "Balance deducted successfully.", new_balance

    @staticmethod
    async def get_recent_transactions(
        db: AsyncSession,
        telegram_user_id: int,
        limit: int = 10
    ) -> list[WalletTransaction]:
        """List customer's recent wallet ledger transactions."""
        stmt = (
            select(WalletTransaction)
            .where(WalletTransaction.telegram_user_id == telegram_user_id)
            .order_by(desc(WalletTransaction.created_at))
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())
