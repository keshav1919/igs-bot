"""
Force Subscription Middleware for aiogram 3.
Ensures no customer can use any bot function without joining the official channel.
"""
import logging
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, TelegramObject

from indo_selling_bot.config import settings
from indo_selling_bot.services.channel_service import ChannelService
from indo_selling_bot.bot.keyboards import (
    CHANNEL_VERIFICATION_TEXT,
    get_channel_verification_keyboard,
)

logger = logging.getLogger("middlewares")

class ForceSubscriptionMiddleware(BaseMiddleware):
    """
    Strict gatekeeper middleware.
    Blocks unverified users from accessing bot commands, buttons, and commerce features.
    """
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user or user.is_bot:
            return await handler(event, data)

        # 1. Administrators always have unrestricted access
        if settings.is_admin(user.id):
            return await handler(event, data)

        bot = data.get("bot")

        # 2. Allow /start message through so handle_start can render start/verify screens
        if isinstance(event, Message):
            if event.text and event.text.startswith("/start"):
                return await handler(event, data)

        # 3. Allow verify and free claim callbacks through to their respective handlers
        if isinstance(event, CallbackQuery):
            if event.data in ("verify_channel", "claim_free_ig"):
                return await handler(event, data)

        # 4. For all other commands, messages, and callbacks, strictly enforce subscription
        is_sub = await ChannelService.is_user_subscribed(bot, user.id)
        if not is_sub:
            if isinstance(event, CallbackQuery):
                await event.answer("❌ Oii Chanel join kr chup Chap 🫵", show_alert=True)
                return None
            elif isinstance(event, Message):
                await event.answer(
                    CHANNEL_VERIFICATION_TEXT,
                    reply_markup=get_channel_verification_keyboard(),
                    parse_mode="HTML"
                )
                return None

        return await handler(event, data)
