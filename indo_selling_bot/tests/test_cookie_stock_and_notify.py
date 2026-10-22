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


def test_notify_keyboard_and_text():
    assert "cookie login Indo igs" in BUY_INDO_NOTIFY_TEXT
    assert "pre Suspend gurente" in BUY_INDO_NOTIFY_TEXT

    kb = get_buy_indo_notify_keyboard()
    flat_btns = [btn for row in kb.inline_keyboard for btn in row]
    btn_texts = [b.text for b in flat_btns]
    btn_cbs = [b.callback_data for b in flat_btns]

    assert "buy  igs" in btn_texts
    assert "open_buy_categories" in btn_cbs


def test_delivery_message_includes_cookie_and_raw_line():
    account = {
        "email": "test@outlook.fr",
        "password": "pwd",
        "username": "user123",
        "cookie": "sessionid=test_cookie_123",
        "raw_line": "test@outlook.fr:pwd:user123:sessionid=test_cookie_123:22 Oct 2026, 22:23:09 IST",
        "domain": "outlook.fr"
    }
    msg = format_delivery_message([account], day_number=1, remaining_stock=5)
    assert "🍪 Cookie : <code>sessionid=test_cookie_123</code>" in msg
    assert "<code>test@outlook.fr:pwd:user123:sessionid=test_cookie_123:22 Oct 2026, 22:23:09 IST</code>" in msg
