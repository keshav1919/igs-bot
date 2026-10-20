"""
Unit tests for aiogram 3 Telegram Bot handlers, commands and screenshot warnings.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, User, Chat, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

from indo_selling_bot.bot.handlers.start import handle_start, handle_help
from indo_selling_bot.bot.handlers.order import handle_screenshot_uploaded

@pytest.mark.asyncio
async def test_bot_start_handler():
    fake_user = User(id=12345, is_bot=False, first_name="Budi", username="budi_id")
    fake_chat = Chat(id=12345, type="private")
    mock_message = AsyncMock(spec=Message)
    mock_message.from_user = fake_user
    mock_message.chat = fake_chat
    mock_message.answer = AsyncMock()

    await handle_start(mock_message)

    assert mock_message.answer.called
    args, kwargs = mock_message.answer.call_args
    welcome_text = args[0]
    assert "Welcome Legend Indo Bot" in welcome_text
    assert "Budi" in welcome_text

@pytest.mark.asyncio
async def test_bot_help_handler():
    fake_user = User(id=12345, is_bot=False, first_name="Budi")
    mock_message = AsyncMock(spec=Message)
    mock_message.from_user = fake_user
    mock_message.answer = AsyncMock()

    await handle_help(mock_message)

    assert mock_message.answer.called
    args, _ = mock_message.answer.call_args
    help_text = args[0]
    assert "/catalog" in help_text
    assert "/orders" in help_text
    assert "Security Reminder" in help_text

@pytest.mark.asyncio
async def test_bot_screenshot_rejection_policy():
    """
    Test requirement: Never treat customer-uploaded screenshots as payment confirmation.
    Verify bot politely informs user and requests textual reference.
    """
    fake_user = User(id=12345, is_bot=False, first_name="Customer")
    mock_message = AsyncMock(spec=Message)
    mock_message.from_user = fake_user
    mock_message.answer = AsyncMock()

    await handle_screenshot_uploaded(mock_message)

    assert mock_message.answer.called
    args, _ = mock_message.answer.call_args
    text = args[0]
    assert "screenshots are NEVER automatically treated as payment confirmation" in text
    assert "type and send the transaction reference string" in text
