import pytest
from datetime import datetime, timezone
from indo_selling_bot.services.stock_txt_service import StockTxtService
from indo_selling_bot.services.stock_import_service import StockImportService
from indo_selling_bot.bot.keyboards import (
    BUY_INDO_NOTIFY_TEXT,
    get_buy_indo_notify_keyboard,
    get_start_welcome_keyboard,
)
from indo_selling_bot.bot.handlers.buy_indo import format_delivery_message


def test_cookie_stock_txt_service_parsing():
    sample = (
        "BrittanieSponenberg182821@outlook.fr:0e8H6Q9ez&N:brittaniesponenberg18282169:"
        "datr=UT_aag5LA2e6kPKSmtpI9G2n; ig_did=BA4B1918-896B-4F65-BCB7-C6A1B6A9C5A8; "
        "mid=ato_UQALAAEkKekwVkDMgnynWZu5; wd=1280x720; csrftoken=1ZyHwz30t2qt7U9prXZx5tk5i5P3pU5j; "
        "ds_user_id=20369491806; sessionid=20369491806%3AS6WfiNUegU7kYD%3A16%3AAYlD9HH4A0YLgHiBKqHyjSed9-o_ToNOcOJ2I51iBg; "
        "ps_l=1; ps_n=1; rur=ODN%2C17841420274206186%2C1793897589%3A01ffadaf7c14fe038e21fe76658162fcf5ca6617ae5dcb54be0d1ff5a3059a1d819988c8:"
        "22 Oct 2026, 22:23:09 IST"
    )
    ref = datetime(2026, 10, 22, 23, 0, 0, tzinfo=timezone.utc)
    parsed = StockTxtService.parse_line(sample, ref_time=ref)

    assert parsed is not None
    assert parsed["email"] == "BrittanieSponenberg182821@outlook.fr"
    assert parsed["password"] == "0e8H6Q9ez&N"
    assert parsed["username"] == "brittaniesponenberg18282169"
    assert "datr=UT_aag5LA2e6kPKSmtpI9G2n;" in parsed["cookie"]
    assert "sessionid=20369491806" in parsed["cookie"]
    assert parsed["day_number"] == 1
    assert parsed["domain"] == "outlook.fr"


def test_cookie_stock_import_service_parsing():
    sample = (
        "CherrylSchiermeister2896@outlook.fr:obfK1g*753:cherrylschiermeister28967:"
        "datr=qz7aauQ_u1SS08ASlbYivwN5; ig_did=13811627-8F77-41D8-89DC-09EC2EE1E6D9; "
        "mid=ato-qwALAAGB_3SBeWAJ7lQLwxvR; wd=1280x720; csrftoken=qg3Z4vdg2wbGLZc4LhHvfPoWArVbsXHF; "
        "ds_user_id=17472075753; sessionid=17472075753%3APQ62oFKY7g50OF%3A9%3AAYmMP4ZP6iIK-P1TWtxV6eDTVW_mEa29_byihMeKFg; "
        "rur=NCG%2C17841417392614009%2C1793897416%3A01ffd9c2c8f5a9b066c9e88409a509d48656f9f9168ed1b274d15f03aca93baff7956f3c:"
        "22 Oct 2026, 22:20:18 IST"
    )
    item_id, cat, raw_dt, status, extra = StockImportService.parse_line_tokens(sample)
    assert item_id == "cherrylschiermeister28967"
    assert extra["mail"] == "CherrylSchiermeister2896@outlook.fr"
    assert extra["password"] == "obfK1g*753"
    assert extra["username"] == "cherrylschiermeister28967"
    assert "datr=qz7aauQ_u1SS08ASlbYivwN5" in extra["cookie"]


@pytest.mark.asyncio
async def test_buy_indo_menu_shows_categories_directly():
    from unittest.mock import AsyncMock
    from indo_selling_bot.bot.handlers.buy_indo import handle_buy_indo_menu

    mock_msg = AsyncMock()
    await handle_buy_indo_menu(mock_msg)

    mock_msg.answer.assert_called_once()
    text = mock_msg.answer.call_args[0][0]
    assert "cookie login Indo igs" not in text
    assert ("Select  Indo" in text) or ("out of stock" in text)


def test_delivery_message_includes_cookie_and_raw_line():
    account = {
        "email": "LemonCaluza350880@outlook.fr",
        "password": "OkW6tz91AW@",
        "username": "lemoncaluza350880",
        "cookie": "sessionid=test_cookie_123",
        "timestamp_str": "22 Oct 2026, 21:59:17 IST",
        "raw_line": "LemonCaluza350880@outlook.fr:OkW6tz91AW@:lemoncaluza350880:sessionid=test_cookie_123:22 Oct 2026, 21:59:17 IST",
        "domain": "outlook.fr"
    }
    msg = format_delivery_message([account], day_number=1, remaining_stock=5)
    assert "#1                   ----  Indo ig✅" in msg
    assert "📧 Email :- <code>LemonCaluza350880@outlook.fr</code>" in msg
    assert "🫟 UserName : - <code>lemoncaluza350880</code>" in msg
    assert "🔑 Password : <code>OkW6tz91AW@</code>" in msg
    assert "cookie : <code>sessionid=test_cookie_123</code>" in msg
    assert "🕒 Created at : <code>22 Oct 2026, 21:59:17 IST</code>" in msg
    assert "<code>LemonCaluza350880@outlook.fr:OkW6tz91AW@:lemoncaluza350880:sessionid=test_cookie_123:22 Oct 2026, 21:59:17 IST</code>" in msg


def test_get_total_stock_count_exists():
    count = StockTxtService.get_total_stock_count()
    assert isinstance(count, int)
    assert count >= 0


@pytest.mark.asyncio
async def test_send_customer_delivery_single():
    from unittest.mock import AsyncMock, MagicMock
    from indo_selling_bot.bot.handlers.buy_indo import send_customer_delivery
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    mock_query = MagicMock()
    mock_message = AsyncMock()
    mock_query.message = mock_message

    account = {
        "email": "LemonCaluza350880@outlook.fr",
        "password": "OkW6tz91AW@",
        "username": "lemoncaluza350880",
        "cookie": "sessionid=test_cookie_123",
        "timestamp_str": "22 Oct 2026, 21:59:17 IST",
        "raw_line": "LemonCaluza350880@outlook.fr:OkW6tz91AW@:lemoncaluza350880:sessionid=test_cookie_123:22 Oct 2026, 21:59:17 IST",
        "domain": "outlook.fr"
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="back")]])
    await send_customer_delivery(
        query=mock_query,
        accounts=[account],
        day_number=1,
        remaining_stock=10,
        order_code="ORD-999999",
        kb=kb
    )

    mock_message.edit_text.assert_called_once()
    called_text = mock_message.edit_text.call_args[0][0]
    assert "INDO STOCK DELIVERY (1 Accounts)" in called_text
    assert "LemonCaluza350880@outlook.fr" in called_text
    assert len(called_text) <= 3800


@pytest.mark.asyncio
async def test_send_customer_delivery_chunked_for_large_order():
    from unittest.mock import AsyncMock, MagicMock
    from indo_selling_bot.bot.handlers.buy_indo import send_customer_delivery
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

    mock_query = MagicMock()
    mock_message = AsyncMock()
    mock_query.message = mock_message

    sample_cookie = "datr=UT_aag5LA2e6kPKSmtpI9G2n; ig_did=BA4B1918-896B-4F65-BCB7-C6A1B6A9C5A8; " * 8
    accounts = []
    for i in range(10):
        accounts.append({
            "email": f"user{i}@outlook.fr",
            "password": f"Pass{i}123@",
            "username": f"user_{i}",
            "cookie": sample_cookie,
            "timestamp_str": "22 Oct 2026, 22:23:09 IST",
            "raw_line": f"user{i}@outlook.fr:Pass{i}123@:user_{i}:{sample_cookie}:22 Oct 2026, 22:23:09 IST",
            "domain": "outlook.fr"
        })

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Back", callback_data="back")]])
    await send_customer_delivery(
        query=mock_query,
        accounts=accounts,
        day_number=1,
        remaining_stock=5,
        order_code="ORD-888888",
        kb=kb
    )

    # Intro message edited to dismiss spinner button
    mock_message.edit_text.assert_called_once()
    intro = mock_message.edit_text.call_args[0][0]
    assert "Delivering your 10 accounts in parts below" in intro

    # Ensure all answer messages are <= 3800 characters
    assert mock_message.answer.call_count >= 2
    for call in mock_message.answer.call_args_list:
        chunk = call[0][0]
        assert len(chunk) <= 3800, f"Chunk exceeded Telegram limit: {len(chunk)} chars"

    # Ensure .txt document file was sent
    mock_message.answer_document.assert_called_once()
    doc_call = mock_message.answer_document.call_args
    assert doc_call.kwargs["document"].filename == "indo_ORD-888888.txt"

