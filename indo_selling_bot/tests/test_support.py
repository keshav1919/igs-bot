"""
Unit tests for customer support ticket ticketing and admin replies.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from indo_selling_bot.services.support_service import SupportService

@pytest.mark.asyncio
async def test_support_ticket_lifecycle(seeded_db: AsyncSession):
    ticket = await SupportService.create_ticket(
        db=seeded_db,
        telegram_user_id=88888,
        subject="Payment inquiry for ORD-123",
        message="I have completed bank transfer, please confirm.",
        telegram_username="customer_dan",
        order_code="ORD-123"
    )
    await seeded_db.commit()

    assert ticket.id is not None
    assert ticket.ticket_code.startswith("TCK-")
    assert ticket.status == "OPEN"
    assert ticket.admin_reply is None

    # Admin replies and marks resolved
    replied = await SupportService.reply_ticket(
        db=seeded_db,
        ticket_id=ticket.id,
        admin_reply="We have received your payment and approved the order.",
        admin_user="support_agent_jane",
        resolve=True
    )
    await seeded_db.commit()

    assert replied.status == "RESOLVED"
    assert replied.admin_reply == "We have received your payment and approved the order."
    assert replied.replied_by == "support_agent_jane"

    # List tickets
    tickets, total, _ = await SupportService.list_tickets(seeded_db)
    assert total >= 1
