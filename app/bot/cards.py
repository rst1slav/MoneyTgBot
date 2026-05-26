"""
High-level "send notification card" helpers for the bot.

Two delivery modes, picked automatically based on `settings.public_base_url`:

1. URL mode (public_base_url is set):
   Bot sends Telegram a URL like
       https://your.app/cards/received.png?amount=8&currency=USDT&usd=%248
   Telegram fetches that URL itself, caches the resulting file_id, and we
   reuse the file_id for subsequent identical URLs.

2. File mode (public_base_url is empty — default for local dev):
   Bot renders the PNG locally to ./cards/ and ships it via FSInputFile.
   Telegram still returns a file_id after the first upload, which we cache
   in memory for instant re-sends of the same card.

Both modes feel the same to the end user. URL mode requires the bot's HTTP
side (app.api) to be reachable from Telegram's servers.
"""

from __future__ import annotations

import urllib.parse
from decimal import Decimal

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup

from app.config import get_settings
from app.services.card_service import (
    RateCard,
    ReceivedCard,
    render_rate_card_to_disk,
    render_to_disk,
)


# Cache: card identity → Telegram file_id. Reused across users.
_card_file_id_cache: dict[str, str] = {}


def _card_key(card: ReceivedCard) -> str:
    return f"received|{card.currency}|{card.amount}|{card.usd_label}"


def _card_url(card: ReceivedCard, base_url: str) -> str:
    qs = urllib.parse.urlencode(
        {"amount": card.amount, "currency": card.currency, "usd": card.usd_label}
    )
    return f"{base_url.rstrip('/')}/cards/received.png?{qs}"


def _format_usd_label(usd_value: Decimal | float | None) -> str:
    if usd_value is None:
        return "$ ?"
    val = float(usd_value)
    if abs(val) >= 100:
        return f"$ {val:.0f}"
    if abs(val) >= 1:
        return f"$ {val:.2f}"
    return f"$ {val:.4f}".rstrip("0").rstrip(".")


def _format_amount(amount: Decimal | float) -> str:
    val = Decimal(str(amount))
    q = val.quantize(Decimal("0.000001"))
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


async def send_received_card(
    *,
    bot: Bot,
    chat_id: int,
    amount: Decimal | float,
    currency: str,
    usd_value: Decimal | float | None = None,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int | None:
    """
    Send a "you received X CCY" notification card. Returns the sent message_id.
    """
    card = ReceivedCard(
        amount=_format_amount(amount),
        currency=currency.upper(),
        usd_label=_format_usd_label(usd_value),
    )
    settings = get_settings()
    cache_key = _card_key(card)

    # Mode 1: URL — Telegram fetches it directly.
    if settings.public_base_url:
        url = _card_url(card, settings.public_base_url)
        cached_id = _card_file_id_cache.get(cache_key)
        photo = cached_id or url
        sent = await bot.send_photo(
            chat_id,
            photo,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if sent.photo and not cached_id:
            _card_file_id_cache[cache_key] = sent.photo[-1].file_id
        return sent.message_id

    # Mode 2: file_id cache + local render.
    cached_id = _card_file_id_cache.get(cache_key)
    if cached_id:
        sent = await bot.send_photo(
            chat_id,
            cached_id,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return sent.message_id

    path = render_to_disk(card)
    sent = await bot.send_photo(
        chat_id,
        FSInputFile(str(path)),
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    if sent.photo:
        _card_file_id_cache[cache_key] = sent.photo[-1].file_id
    return sent.message_id


# ---------------------------------------------------------------------------
# Rate card (TON/USD chart-style notification)
# ---------------------------------------------------------------------------


def _rate_card_url(card: RateCard, base_url: str) -> str:
    qs = urllib.parse.urlencode(
        {
            "base": card.base,
            "quote": card.quote,
            "price": f"{card.price:.6g}",
            "change": f"{card.change_pct:.2f}",
            "prices": ",".join(f"{p:.6g}" for p in card.history),
            "dates": ",".join(card.date_labels),
        }
    )
    return f"{base_url.rstrip('/')}/cards/rate.png?{qs}"


async def send_rate_card(
    *,
    bot: Bot,
    chat_id: int,
    base: str,
    quote: str,
    price: Decimal | float,
    change_pct: Decimal | float,
    history: list[float] | list[Decimal] | None = None,
    date_labels: list[str] | None = None,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "HTML",
) -> int | None:
    """
    Send a rate card. Two modes (same auto-switch as send_received_card):
      - URL mode if PUBLIC_BASE_URL is set;
      - file_id-cached local-render mode otherwise.
    """
    card = RateCard(
        base=base.upper(),
        quote=quote.upper(),
        price=float(price),
        change_pct=float(change_pct),
        history=[float(x) for x in (history or [])],
        date_labels=list(date_labels or []),
    )
    settings = get_settings()
    cache_key = card.cache_key()

    if settings.public_base_url:
        url = _rate_card_url(card, settings.public_base_url)
        cached_id = _card_file_id_cache.get(cache_key)
        sent = await bot.send_photo(
            chat_id,
            cached_id or url,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        if sent.photo and not cached_id:
            _card_file_id_cache[cache_key] = sent.photo[-1].file_id
        return sent.message_id

    cached_id = _card_file_id_cache.get(cache_key)
    if cached_id:
        sent = await bot.send_photo(
            chat_id,
            cached_id,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
        return sent.message_id

    path = render_rate_card_to_disk(card)
    sent = await bot.send_photo(
        chat_id,
        FSInputFile(str(path)),
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    if sent.photo:
        _card_file_id_cache[cache_key] = sent.photo[-1].file_id
    return sent.message_id
