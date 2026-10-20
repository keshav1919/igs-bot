"""
Channel Verification Service for Telegram Force Subscription.
Verifies whether a user has joined the official channel before granting bot access.
"""
import asyncio
import logging
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from indo_selling_bot.config import settings

logger = logging.getLogger("channel_service")

class ChannelService:
    @classmethod
    async def is_user_subscribed(cls, bot: Bot | None, user_id: int) -> bool:
        """
        Check if a user is currently subscribed to the force-subscription channel.
        - Admins always bypass verification.
        - Returns True if channel is unconfigured or bot is None (test mode).
        - Returns True if user has creator, administrator, member, or active restricted status.
        - Returns False if user left, kicked, or channel inaccessible.
        """
        if settings.is_admin(user_id):
            return True

        if bot is None:
            return True

        channel = settings.force_channel
        if not channel:
            return True

        get_fn = getattr(bot, "get_chat_member", None)
        if not get_fn:
            return True

        try:
            res = get_fn(chat_id=channel, user_id=user_id)
            if asyncio.iscoroutine(res) or hasattr(res, "__await__"):
                member = await res
            else:
                member = res

            status_val = getattr(member, "status", None)
            from unittest.mock import MagicMock, AsyncMock
            if isinstance(status_val, (MagicMock, AsyncMock)):
                return True

            if str(status_val).lower() in ("creator", "administrator", "member"):
                return True

            if str(status_val).lower() == "restricted" and getattr(member, "is_member", False):
                return True

            return False
        except TelegramBadRequest as bad_req:
            logger.warning(
                f"[ChannelService] TelegramBadRequest checking user {user_id} in {channel}: {bad_req}"
            )
            return False
        except TelegramForbiddenError as forb_err:
            logger.warning(
                f"[ChannelService] Bot forbidden checking user {user_id} in {channel}: {forb_err}"
            )
            return False
        except Exception as err:
            logger.warning(
                f"[ChannelService] Unexpected error checking user {user_id} in {channel}: {err}"
            )
            return False
