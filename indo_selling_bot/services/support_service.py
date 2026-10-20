"""
Customer support ticketing service for Telegram inquiries and admin replies.
"""
import secrets
from datetime import datetime, timezone
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from indo_selling_bot.models.support import SupportTicket

class SupportService:
    @staticmethod
    def generate_ticket_code() -> str:
        rand_str = secrets.token_hex(3).upper()
        return f"TCK-{rand_str}"

    @staticmethod
    async def create_ticket(
        db: AsyncSession,
        telegram_user_id: int,
        subject: str,
        message: str,
        telegram_username: str | None = None,
        order_code: str | None = None
    ) -> SupportTicket:
        """Create a new support ticket from customer."""
        ticket = SupportTicket(
            ticket_code=SupportService.generate_ticket_code(),
            telegram_user_id=telegram_user_id,
            telegram_username=telegram_username,
            order_code=order_code.strip() if order_code else None,
            subject=subject.strip(),
            message=message.strip(),
            status="OPEN",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        db.add(ticket)
        await db.flush()
        return ticket

    @staticmethod
    async def get_by_id(db: AsyncSession, ticket_id: int) -> SupportTicket | None:
        stmt = select(SupportTicket).where(SupportTicket.id == ticket_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def reply_ticket(
        db: AsyncSession,
        ticket_id: int,
        admin_reply: str,
        admin_user: str,
        resolve: bool = False
    ) -> SupportTicket | None:
        """Admin replies to customer support ticket."""
        ticket = await SupportService.get_by_id(db, ticket_id)
        if not ticket:
            return None

        ticket.admin_reply = admin_reply.strip()
        ticket.replied_by = admin_user
        ticket.replied_at = datetime.now(timezone.utc)
        ticket.status = "RESOLVED" if resolve else "REPLIED"
        ticket.updated_at = datetime.now(timezone.utc)
        await db.flush()
        return ticket

    @staticmethod
    async def list_tickets(
        db: AsyncSession,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20
    ) -> tuple[list[SupportTicket], int, int]:
        stmt = select(SupportTicket)
        count_stmt = select(func.count(SupportTicket.id))

        if status:
            stmt = stmt.where(SupportTicket.status == status)
            count_stmt = count_stmt.where(SupportTicket.status == status)

        stmt = stmt.order_by(desc(SupportTicket.created_at))

        total_count = (await db.execute(count_stmt)).scalar_one() or 0
        total_pages = max(1, (total_count + per_page - 1) // per_page)
        offset = max(0, (page - 1) * per_page)
        stmt = stmt.offset(offset).limit(per_page)

        result = await db.execute(stmt)
        tickets = list(result.scalars().all())
        return tickets, total_count, total_pages
