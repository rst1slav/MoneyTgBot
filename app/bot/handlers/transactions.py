import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
import time as _time
import uuid

import httpx
from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
    Message,
)
from sqlalchemy import and_, select

from app.bot.handlers.history import render_history, _user_history_filters
from app.bot.handlers.integrations import render_integrations_menu
from app.bot.handlers.profile import (
    _ensure_first_wallet,
    build_profile_text_for_inline,
    render_crypto_main,
    render_profile,
)
from app.bot.keyboards import back_home_keyboard, yona_main_menu_keyboard
from app.db.models import AccountType
from app.bot.panel import push_text_panel
from app.config import get_settings
from app.db.session import SessionLocal
from app.services.fx_service import FxService
from app.services.history_service import HistoryFilters
from app.services.ledger_service import LedgerService
from app.services.report_service import ReportService
from app.services.ton_service import TonService

router = Router(name="transactions")
ledger = LedgerService()
fx_service = FxService()
ton_service = TonService()
report_service = ReportService()


_start_in_flight: set[int] = set()
_SUPPORTED_CCY = {"TON", "UAH", "RUB", "USD", "EUR", "USDT", "BYN", "PLN", "UZS"}
_ALSO_LIST = ["EUR", "UAH", "RUB", "BYN", "PLN", "UZS"]

# Aliases — каждый сводится к каноническому ISO коду.
_CCY_ALIASES: dict[str, str] = {
    # USD
    "USD": "USD", "ЮСД": "USD", "ЮСДИ": "USD", "ДОЛЛАР": "USD", "ДОЛЛАРЫ": "USD",
    "ДОЛЛАРОВ": "USD", "ДОЛЛАРА": "USD", "ДОЛ": "USD", "БАКС": "USD", "БАКСОВ": "USD",
    # USDT
    "USDT": "USDT", "ТЕЗЕР": "USDT", "ТЕТЕР": "USDT", "ЮСДТ": "USDT",
    # EUR
    "EUR": "EUR", "ЕВРО": "EUR", "ЕВР": "EUR",
    # UAH
    "UAH": "UAH", "ГРН": "UAH", "ГРИВНА": "UAH", "ГРИВНЫ": "UAH",
    "ГРИВЕН": "UAH", "ГРИВНУ": "UAH", "ГРИВНІ": "UAH",
    # RUB
    "RUB": "RUB", "РУБ": "RUB", "РУБЛЬ": "RUB", "РУБЛИ": "RUB",
    "РУБЛЕЙ": "RUB", "РУБЛЯ": "RUB", "РОССРУБ": "RUB",
    # BYN
    "BYN": "BYN", "БУН": "BYN", "БЕЛРУБ": "BYN", "БЕЛОРУСРУБ": "BYN",
    "БЕЛРУБЛЬ": "BYN", "БЕЛРУБЛИ": "BYN", "ЗАЯЦ": "BYN",
    # PLN
    "PLN": "PLN", "ЗЛОТЫЙ": "PLN", "ЗЛОТ": "PLN", "ЗЛОТЫХ": "PLN",
    "ЗЛОТЫЕ": "PLN", "ПЛН": "PLN",
    # UZS
    "UZS": "UZS", "СУМ": "UZS", "СУМЫ": "UZS", "СУММ": "UZS",
    "СУМОВ": "UZS", "УЗБСУМ": "UZS",
    # TON
    "TON": "TON", "ТОН": "TON", "ТОНКОИН": "TON", "ТОНКОИНЫ": "TON",
}

# Multi-word phrases — нормализуются до канонического кода ДО токенизации.
_MULTI_WORD_ALIASES: list[tuple[str, str]] = [
    ("белорусский рубль", "BYN"),
    ("белорусских рублей", "BYN"),
    ("бел рубль", "BYN"),
    ("бел рубли", "BYN"),
    ("бел рублей", "BYN"),
    ("бел руб", "BYN"),
    ("польский злотый", "PLN"),
    ("польских злотых", "PLN"),
    ("польский злот", "PLN"),
    ("узбекский сум", "UZS"),
    ("узбекских сумов", "UZS"),
    ("узбекских сум", "UZS"),
    ("узб сум", "UZS"),
]


def _normalize_ccy(token: str) -> str | None:
    return _CCY_ALIASES.get(token.upper().strip())


def _preprocess_query(raw: str) -> str:
    s = raw.lower().strip()
    for phrase, code in _MULTI_WORD_ALIASES:
        s = s.replace(phrase, code.lower())
    return s

# Caches for currency rates and TON 7d % change.
_er_rates_cache: tuple[dict, float] | None = None
_ER_TTL = 60
_ton_pct_cache: tuple[Decimal | None, float] | None = None
_TON_PCT_TTL = 300

# Inline refresh-button rate limit (per user).
_FX_REFRESH_COOLDOWN = 60
_last_fx_refresh: dict[int, float] = {}


async def _er_rates() -> dict | None:
    global _er_rates_cache
    now = _time.time()
    if _er_rates_cache and now - _er_rates_cache[1] < _ER_TTL:
        return _er_rates_cache[0]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://open.er-api.com/v6/latest/USD")
            r.raise_for_status()
            payload = r.json()
    except Exception:
        return None
    rates = payload.get("rates", {}) if isinstance(payload, dict) else {}
    _er_rates_cache = (rates, now)
    return rates


async def _usd_rate_for(symbol: str) -> Decimal | None:
    s = symbol.upper().strip()
    if s in {"USD", "USDT"}:
        return Decimal("1")
    if s == "TON":
        rate = await ton_service.ton_price_usd()
        if rate:
            await _save_rate_snapshot_if_due(s, rate)
        return rate

    # Fiat first — never let a CoinGecko ticker collision (e.g. some scam coin
    # named "RUB") override a real currency.
    rates = await _er_rates()
    if rates:
        val = rates.get(s)
        if val:
            try:
                rate = Decimal("1") / Decimal(str(val))
                await _save_rate_snapshot_if_due(s, rate)
                return rate
            except Exception:
                pass

    # Skip CoinGecko entirely for known fiat tickers (er-api transient miss
    # shouldn't fall through to crypto data).
    if s in _FIAT_TICKERS_SET:
        return None

    # Crypto fallback — covers BTC/ETH/SOL plus jetton memecoins (DOGS/NOT/MAJOR/etc.).
    crypto_rate = await _cg_crypto_rate(s)
    if crypto_rate is not None:
        await _save_rate_snapshot_if_due(s, crypto_rate)
        return crypto_rate
    return None


# CoinGecko ticker → USD price (cached). 5-minute TTL; None when nothing matches.
_cg_rate_cache: dict[str, tuple[Decimal | None, float]] = {}
_CG_TTL = 300
# Manual ticker → coin-id map for tickers ambiguous on CoinGecko search.
_CG_TICKER_OVERRIDES: dict[str, str] = {
    "TON":   "the-open-network",
    "BTC":   "bitcoin",
    "ETH":   "ethereum",
    "SOL":   "solana",
    "BNB":   "binancecoin",
    "XRP":   "ripple",
    "ADA":   "cardano",
    "DOGE":  "dogecoin",
    "TRX":   "tron",
    "LINK":  "chainlink",
    "AVAX":  "avalanche-2",
    "MATIC": "matic-network",
    "DOT":   "polkadot",
    "LTC":   "litecoin",
    "DOGS":  "dogs-2",
    "NOT":   "notcoin",
    "MAJOR": "major",
    "HMSTR": "hamster-kombat",
    "CATI":  "catizen",
    "WIF":   "dogwifcoin",
    "PEPE":  "pepe",
}


async def _cg_crypto_rate(ticker: str) -> Decimal | None:
    """CoinGecko USD price for `ticker`. Cached for 5 min."""
    s = ticker.upper().strip()
    now = _time.time()
    cached = _cg_rate_cache.get(s)
    if cached and (now - cached[1]) < _CG_TTL:
        return cached[0]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            coin_id = _CG_TICKER_OVERRIDES.get(s)
            if not coin_id:
                r = await client.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": s},
                )
                r.raise_for_status()
                payload = r.json() or {}
                # Pick first symbol-exact match (preferring market_cap_rank if any).
                best: dict | None = None
                for c in payload.get("coins", []) or []:
                    if (c.get("symbol") or "").upper() != s:
                        continue
                    if best is None:
                        best = c
                        continue
                    a = c.get("market_cap_rank") or 10**9
                    b = best.get("market_cap_rank") or 10**9
                    if a < b:
                        best = c
                coin_id = best.get("id") if best else None
            if not coin_id:
                _cg_rate_cache[s] = (None, now)
                return None
            r2 = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            )
            r2.raise_for_status()
            price = ((r2.json() or {}).get(coin_id) or {}).get("usd")
            if price is None:
                _cg_rate_cache[s] = (None, now)
                return None
            rate = Decimal(str(price))
    except Exception:
        _cg_rate_cache[s] = (None, now)
        return None
    _cg_rate_cache[s] = (rate, now)
    return rate


async def _save_rate_snapshot_if_due(ccy: str, usd_rate: Decimal) -> None:
    """Daily snapshot of USD rate per currency for period-over-period % deltas."""
    if ccy in {"USD", "USDT"}:
        return  # constant 1; no need to record
    from app.db.models import DailyRateSnapshot  # local import avoids cycles

    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    async with SessionLocal() as db:
        existing = (
            await db.execute(
                select(DailyRateSnapshot.id).where(
                    and_(
                        DailyRateSnapshot.ccy_code == ccy,
                        DailyRateSnapshot.snapshot_at >= today_start,
                    )
                ).limit(1)
            )
        ).scalar_one_or_none()
        if existing:
            return
        db.add(DailyRateSnapshot(
            ccy_code=ccy,
            usd_rate=usd_rate,
            snapshot_at=datetime.utcnow(),
        ))
        try:
            await db.commit()
        except Exception:
            pass


async def _historic_usd_rate(ccy: str, days_ago: int = 7) -> Decimal | None:
    """Returns the USD rate for `ccy` from approximately `days_ago` days ago."""
    if ccy in {"USD", "USDT"}:
        return Decimal("1")
    from app.db.models import DailyRateSnapshot

    target = datetime.utcnow() - timedelta(days=days_ago)
    tolerance = max(2, days_ago // 5)
    earliest = target - timedelta(days=tolerance)
    latest = target + timedelta(days=tolerance)
    async with SessionLocal() as db:
        row = (
            await db.execute(
                select(DailyRateSnapshot.usd_rate).where(
                    and_(
                        DailyRateSnapshot.ccy_code == ccy,
                        DailyRateSnapshot.snapshot_at >= earliest,
                        DailyRateSnapshot.snapshot_at <= latest,
                    )
                ).order_by(DailyRateSnapshot.snapshot_at.asc()).limit(1)
            )
        ).scalar_one_or_none()
        return Decimal(row) if row is not None else None


async def _ton_7d_pct() -> Decimal | None:
    """Returns TON 7-day price change percentage in USD (e.g. Decimal('28.5'))."""
    global _ton_pct_cache
    now = _time.time()
    if _ton_pct_cache and now - _ton_pct_cache[1] < _TON_PCT_TTL:
        return _ton_pct_cache[0]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/coins/the-open-network",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "market_data": "true",
                    "community_data": "false",
                    "developer_data": "false",
                    "sparkline": "false",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        _ton_pct_cache = (None, now)
        return None
    pct_raw = (data.get("market_data") or {}).get("price_change_percentage_7d")
    if pct_raw is None:
        _ton_pct_cache = (None, now)
        return None
    try:
        pct = Decimal(str(pct_raw))
    except Exception:
        pct = None
    _ton_pct_cache = (pct, now)
    return pct


def _fmt_amount(value: Decimal) -> str:
    """
    Up to 5 decimal places; trailing zeros trimmed; thousand-spaces in integer part.
    Examples:
      2.47        → "2.47"
      1.00000     → "1"
      2384.5      → "2 384.5"
      0.000123456 → "0.00012"
      101.23456   → "101.23456"
    """
    sign = "-" if value < 0 else ""
    abs_v = abs(value)
    rounded = f"{abs_v:.5f}"
    int_part, frac_part = rounded.split(".") if "." in rounded else (rounded, "")
    frac_part = frac_part.rstrip("0")
    if len(int_part) > 3:
        try:
            int_part = f"{int(int_part):,}".replace(",", " ")
        except ValueError:
            pass
    if frac_part:
        return f"{sign}{int_part}.{frac_part}"
    return f"{sign}{int_part}"


# Backwards-compatible aliases — both main and "Также" lines use the same format now.
_fmt_main = _fmt_amount
_fmt_also = _fmt_amount


def _parse_inline_query(raw: str) -> tuple[Decimal, str, str | None] | None:
    """
    Parses query into (amount, base, target_or_None).
    Patterns supported (currency tokens may be cyrillic aliases):
      <ccy>                    → 1 of ccy, no explicit target
      <ccy> <ccy>              → 1, base, target
      <num> <ccy>              → num, base, no explicit target
      <num> <ccy> <ccy>        → num, base, target
    """
    s = _preprocess_query(raw)
    parts = [p for p in s.upper().split() if p]
    if not parts or len(parts) > 3:
        return None
    amount = Decimal("1")
    first = parts[0].replace(",", ".")
    try:
        amount = Decimal(first)
        if amount <= 0:
            return None
        ccy_parts = parts[1:]
    except Exception:
        ccy_parts = parts
    if not ccy_parts or len(ccy_parts) > 2:
        return None
    base = _normalize_ccy(ccy_parts[0])
    target = _normalize_ccy(ccy_parts[1]) if len(ccy_parts) >= 2 else None
    if not base:
        return None
    if len(ccy_parts) >= 2 and not target:
        return None
    return amount, base, target


async def _build_conversion_text(
    amount: Decimal,
    base: str,
    target: str | None,
    *,
    default_target: str = "USD",
    lang: str = "ru",
) -> str | None:
    """
    Renders the inline-conversion message body. When `target` is None, falls back
    to `default_target` (typically the user's primary currency from settings).
    """
    from app.i18n import t as _t

    base_usd = await _usd_rate_for(base)
    if base_usd is None or base_usd <= 0:
        return None

    explicit_target = target is not None
    if not target:
        target = default_target or "USD"

    target_usd = await _usd_rate_for(target)
    if target_usd is None or target_usd <= 0:
        # Fallback if the user's preferred currency isn't supported by our rate API.
        target = "USD"
        target_usd = await _usd_rate_for(target)
        if target_usd is None or target_usd <= 0:
            return None

    rate = base_usd / target_usd
    target_amount = amount * rate

    pct: Decimal | None = None
    base_past = await _historic_usd_rate(base, days_ago=7)
    target_past = await _historic_usd_rate(target, days_ago=7)
    if (
        base_past is not None and target_past is not None
        and base_past > 0 and target_past > 0
    ):
        past_rate = base_past / target_past
        if past_rate > 0:
            pct = (rate - past_rate) / past_rate * Decimal("100")
    if pct is None and "TON" in {base, target}:
        ton_pct = await _ton_7d_pct()
        if ton_pct is not None:
            if base == "TON" and target != "TON":
                pct = ton_pct
            elif target == "TON" and base != "TON":
                pct = -ton_pct

    icon = "🟢" if (pct is None or pct >= 0) else "🔴"
    if pct is not None:
        sign = "+" if pct >= 0 else ""
        pct_str = f" ({sign}{int(round(pct))}%)"
    else:
        pct_str = ""

    # Single-line conversion — the "Also: …" alternates row was removed per feedback.
    return (
        f"<b>{icon} {_fmt_main(amount)} {base} = "
        f"{_fmt_main(target_amount)} {target}{pct_str}</b>"
    )


def _fx_refresh_keyboard(
    amount: Decimal, base: str, target: str | None, lang: str = "ru"
) -> InlineKeyboardMarkup:
    from app.i18n import t as _t

    amt_str = format(amount.normalize(), "f")
    if amt_str.endswith("."):
        amt_str = amt_str[:-1]
    target_str = target or "-"
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=_t("inline.refresh", lang),
                callback_data=f"fxupd:{amt_str}:{base}:{target_str}",
            )
        ]]
    )


_fx_chart_file_cache: dict[tuple[str, str, str], tuple[str, float]] = {}
_FX_CHART_TTL = 1800  # 30 min


async def _fetch_ton_binance(days: int = 7) -> list[tuple[datetime, float]]:
    """
    Binance klines for TONUSDT at 30-minute intervals.
    Free, no auth required, supports custom interval+range.
    days=7 → 336 points (7 * 48 half-hours).
    """
    limit = max(1, min(1000, days * 48))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": "TONUSDT", "interval": "30m", "limit": str(limit)},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: list[tuple[datetime, float]] = []
    for kline in data:
        if not isinstance(kline, list) or len(kline) < 5:
            continue
        try:
            ts = datetime.utcfromtimestamp(int(kline[0]) / 1000)
            close = float(kline[4])
            if close > 0:
                out.append((ts, close))
        except (TypeError, ValueError):
            continue
    return out


async def _fetch_ton_coingecko(days: int = 7) -> list[tuple[datetime, float]]:
    """
    CoinGecko fallback for TON. Free-tier granularity is auto:
      days=1     → 5-min, days=2-90 → hourly, days=91+ → daily.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/coins/the-open-network/market_chart",
                params={"vs_currency": "usd", "days": str(days)},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return []
    pts = data.get("prices") or []
    return [(datetime.utcfromtimestamp(int(t) / 1000), float(p)) for t, p in pts]


async def _fetch_ton_history(days: int = 7) -> list[tuple[datetime, float]]:
    """
    Tries Binance (30-min, supports 7d natively); falls back to CoinGecko if it
    fails (e.g. blocked region).
    """
    pts = await _fetch_ton_binance(days=days)
    if pts:
        return pts
    return await _fetch_ton_coingecko(days=days)


_cg_history_cache: dict[tuple[str, int], tuple[list, float]] = {}
_CG_HISTORY_TTL = 60 * 60          # 1h — market_chart is heavy and rate-limited
_cg_id_cache: dict[str, str | None] = {}   # ticker → coin_id (or None if not found)
_log = logging.getLogger(__name__)


async def _fetch_cg_history(ticker: str, days: int) -> list[tuple[datetime, float]]:
    """USD-price history for any CoinGecko-listed coin (including memcoins).

    Aggressively cached (1h) so the same pair doesn't burn through CG's free
    rate-limit on every render. Logs failures explicitly.
    """
    s = ticker.upper().strip()
    cache_key = (s, days)
    now = _time.time()
    cached = _cg_history_cache.get(cache_key)
    if cached and (now - cached[1]) < _CG_HISTORY_TTL:
        return cached[0]

    coin_id = _CG_TICKER_OVERRIDES.get(s) or _cg_id_cache.get(s)
    if not coin_id and s not in _cg_id_cache:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://api.coingecko.com/api/v3/search",
                    params={"query": s},
                )
                r.raise_for_status()
                payload = r.json() or {}
            best: dict | None = None
            for c in payload.get("coins", []) or []:
                if (c.get("symbol") or "").upper() != s:
                    continue
                if best is None or (c.get("market_cap_rank") or 10**9) < (best.get("market_cap_rank") or 10**9):
                    best = c
            coin_id = best.get("id") if best else None
            _cg_id_cache[s] = coin_id
        except Exception as exc:
            _log.warning("CoinGecko search failed for %s: %s", s, exc)
            _cg_id_cache[s] = None
            _cg_history_cache[cache_key] = ([], now)
            return []
    if not coin_id:
        _cg_history_cache[cache_key] = ([], now)
        return []

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": str(days)},
            )
            if r.status_code == 429:
                _log.warning("CoinGecko rate-limited fetching market_chart for %s", s)
                _cg_history_cache[cache_key] = ([], now)
                return []
            r.raise_for_status()
            payload = r.json() or {}
    except Exception as exc:
        _log.warning("CoinGecko market_chart failed for %s: %s", s, exc)
        _cg_history_cache[cache_key] = ([], now)
        return []
    points = payload.get("prices") or []
    series = [
        (datetime.utcfromtimestamp(int(t) / 1000), float(p))
        for t, p in points if p
    ]
    _cg_history_cache[cache_key] = (series, now)
    return series


# fawazahmed0/exchange-api on jsDelivr — wide currency coverage including RUB/UAH/BYN.
_FIAT_HISTORY_CACHE: dict[tuple[str, str], tuple[float, float]] = {}  # (ticker, date_iso) → (usd_per_ticker, fetched_at)
_FIAT_HISTORY_TTL = 6 * 3600  # 6h — daily data refreshes once a day server-side


async def _fetch_fiat_history(ticker: str, days: int) -> list[tuple[datetime, float]]:
    """Daily USD-per-ticker for the last `days` days via fawazahmed0 CDN.
    All daily fetches share a single httpx client; failed days are silently
    dropped, missing currencies are logged once.
    """
    s = ticker.lower().strip()
    if s in {"usd", "usdt"}:
        return []
    today = datetime.utcnow().date()
    dates = [today - timedelta(days=days - 1 - i) for i in range(days)]
    now = _time.time()

    async with httpx.AsyncClient(timeout=8) as client:
        async def _one(date) -> tuple[datetime, float] | None:
            key = (s, date.isoformat())
            cached = _FIAT_HISTORY_CACHE.get(key)
            if cached and (now - cached[1]) < _FIAT_HISTORY_TTL:
                return (datetime.combine(date, datetime.min.time().replace(hour=12)), cached[0])
            url = (
                f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{date.isoformat()}"
                "/v1/currencies/usd.json"
            )
            try:
                r = await client.get(url)
                r.raise_for_status()
                payload = r.json() or {}
            except Exception:
                return None
            usd_rates = payload.get("usd") or {}
            per = usd_rates.get(s)
            if not per:
                return None
            try:
                usd_per_ticker = 1.0 / float(per)
            except Exception:
                return None
            _FIAT_HISTORY_CACHE[key] = (usd_per_ticker, now)
            return (datetime.combine(date, datetime.min.time().replace(hour=12)), usd_per_ticker)

        results = await asyncio.gather(*[_one(d) for d in dates])
    out = [r for r in results if r is not None]
    if not out:
        _log.info("Fiat history empty for %s (%d days)", s.upper(), days)
    return out


_FIAT_TICKERS_SET: set[str] = {
    "USD", "EUR", "GBP", "JPY", "CNY", "RUB", "UAH", "BYN", "PLN", "UZS",
    "CAD", "AUD", "CHF", "KZT", "TRY", "INR", "KRW", "BRL", "MXN", "HKD",
    "SGD", "NOK", "SEK", "DKK", "CZK", "HUF", "RON", "BGN", "ZAR", "NZD",
    "ILS", "AED", "SAR", "ARS", "CLP", "COP", "PEN", "EGP", "NGN", "VND",
    "THB", "MYR", "IDR", "PHP", "PKR", "BDT",
}


async def _fetch_usd_history(ticker: str, days: int) -> list[tuple[datetime, float]]:
    """USD per `ticker` over the last `days`. Picks the right source per ticker class.

    Order matters: fiat tickers must NOT be searched on CoinGecko — otherwise a
    scam coin sharing the ticker (e.g. several listings use the symbol "RUB") can
    hijack the lookup and return microscopic prices. Fiat goes straight to the
    fawazahmed0 currency CDN.
    """
    s = ticker.upper().strip()
    if s in {"USD", "USDT"}:
        now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        return [(now - timedelta(days=days - 1 - i), 1.0) for i in range(days)]

    if s == "TON":
        return await _fetch_ton_history(days)

    # Fiat path first — never let CoinGecko intercept a real currency ticker.
    if s in _FIAT_TICKERS_SET:
        fiat = await _fetch_fiat_history(s, days)
        if fiat:
            return fiat
        # Even fiat may transiently fail (CDN hiccup); fall through to flat-line.
    else:
        # Crypto via CoinGecko (covers BTC, ETH, SOL, DOGS, NOT, MAJOR, …).
        cg = await _fetch_cg_history(s, days)
        if cg:
            return cg

    # Last resort: pad current rate as a flat line so the chart is never empty.
    current = await _usd_rate_for(s)
    if current is not None:
        now = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
        return [(now - timedelta(days=days - 1 - i), float(current)) for i in range(days)]
    return []


def _combine_usd_histories(
    base_h: list[tuple[datetime, float]],
    target_h: list[tuple[datetime, float]],
) -> list[tuple[datetime, float]]:
    """Combine base/target USD histories into base→target rate at each timestamp."""
    if not base_h or not target_h:
        return []
    # Use whichever series has more points as the primary time grid.
    primary, secondary, primary_is_base = (
        (base_h, target_h, True) if len(base_h) >= len(target_h)
        else (target_h, base_h, False)
    )
    sec_sorted = sorted(secondary, key=lambda x: x[0])
    out: list[tuple[datetime, float]] = []
    for dt, v in sorted(primary, key=lambda x: x[0]):
        # Nearest-timestamp lookup in the secondary series.
        best = min(sec_sorted, key=lambda x: abs((x[0] - dt).total_seconds()))
        b = v if primary_is_base else best[1]
        t = best[1] if primary_is_base else v
        if t > 0:
            out.append((dt, b / t))
    return out


async def _fx_history(base: str, target: str, days: int = 7) -> list[tuple[datetime, float]]:
    """
    Returns [(datetime, base→target rate)] over the last `days`.
    Pulls real history for every ticker class (TON, other crypto via CoinGecko,
    fiat via fawazahmed0 CDN), so even fiat-fiat pairs get a real 7-day chart.
    """
    base_u = base.upper().strip()
    target_u = target.upper().strip()
    if base_u == target_u:
        return []
    if base_u in {"USD", "USDT"} and target_u in {"USD", "USDT"}:
        return []  # constant 1:1, chart adds no value

    base_h, target_h = await asyncio.gather(
        _fetch_usd_history(base_u, days),
        _fetch_usd_history(target_u, days),
    )
    return _combine_usd_histories(base_h, target_h)


_fx_chart_in_progress: dict[tuple[str, str, str], float] = {}
_FX_INPROGRESS_TIMEOUT = 60  # auto-clear stale locks
_fx_render_lock = asyncio.Lock()  # serialize matplotlib (not thread-safe)


def _cached_fx_file_id(base: str, target: str, lang: str) -> str | None:
    """Returns the cached file_id if fresh; never blocks or fetches."""
    key = (base, target, lang)
    cached = _fx_chart_file_cache.get(key)
    if cached and _time.time() - cached[1] < _FX_CHART_TTL:
        return cached[0]
    return None


_MONTHS_BY_LANG: dict[str, list[str]] = {
    "en": ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
    "ru": ["ЯНВ", "ФЕВ", "МАР", "АПР", "МАЯ", "ИЮН", "ИЮЛ", "АВГ", "СЕН", "ОКТ", "НОЯ", "ДЕК"],
    "uk": ["СІЧ", "ЛЮТ", "БЕР", "КВІ", "ТРА", "ЧЕР", "ЛИП", "СЕР", "ВЕР", "ЖОВ", "ЛИС", "ГРУ"],
}


def _fmt_date_short(dt: datetime, lang: str) -> str:
    months = _MONTHS_BY_LANG.get(lang, _MONTHS_BY_LANG["en"])
    return f"{months[dt.month - 1]} {dt.day}"


def _evenly_spaced_labels(
    points: list[tuple[datetime, float]], lang: str, count: int = 4
) -> list[str]:
    if not points:
        return []
    if len(points) <= count:
        return [_fmt_date_short(dt, lang) for dt, _ in points]
    step = (len(points) - 1) / (count - 1)
    idxs = [int(round(i * step)) for i in range(count)]
    return [_fmt_date_short(points[i][0], lang) for i in idxs]


async def _build_and_cache_fx_chart(bot, base: str, target: str, lang: str) -> str | None:
    """
    Generates a rate-style card for `base/target`, uploads it to the storage
    chat for an inline-mode file_id, then caches the file_id.

    Auto-fails fast — only one task per (base, target, lang) gets to render at
    a time (60s stale-lock timeout).
    """
    key = (base, target, lang)
    now = _time.time()

    in_prog_at = _fx_chart_in_progress.get(key)
    if in_prog_at and (now - in_prog_at) < _FX_INPROGRESS_TIMEOUT:
        return None
    _fx_chart_in_progress[key] = now

    try:
        prices = await _fx_history(base, target, days=7)
        if not prices:
            # Final guaranteed fallback: build a flat 7-day series from the
            # current rate so we always have *something* to render.
            base_usd = await _usd_rate_for(base)
            target_usd = await _usd_rate_for(target)
            if base_usd and target_usd and target_usd > 0:
                current_rate = float(base_usd / target_usd)
                now_dt = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
                prices = [
                    (now_dt - timedelta(days=6 - i), current_rate)
                    for i in range(7)
                ]
                _log.info("FX chart flat-line fallback for %s/%s (no history data)", base, target)
            else:
                _log.warning("FX chart skipped for %s/%s: no rate sources", base, target)
                return None

        from app.services.card_service import RateCard, render_rate_card_to_disk

        ys = [p[1] for p in prices]
        current = ys[-1]
        first = ys[0]
        change_pct = ((current - first) / first * 100.0) if first > 0 else 0.0
        # Card design uses English month abbreviations regardless of UI lang.
        labels = _evenly_spaced_labels(prices, "en")

        card = RateCard(
            base=base,
            quote=target,
            price=float(current),
            change_pct=float(change_pct),
            history=[float(y) for y in ys],
            date_labels=labels,
        )

        async with _fx_render_lock:
            try:
                path = render_rate_card_to_disk(card)
            except Exception as exc:
                _log.exception("FX chart render failed for %s/%s: %s", base, target, exc)
                return None

        storage_chat_id = get_settings().inline_storage_chat_id
        try:
            msg = await bot.send_photo(
                chat_id=storage_chat_id,
                photo=FSInputFile(str(path)),
            )
        except Exception as exc:
            _log.warning("FX chart upload to storage chat failed for %s/%s: %s", base, target, exc)
            return None

        file_id = msg.photo[-1].file_id if msg.photo else None
        if file_id:
            _fx_chart_file_cache[key] = (file_id, _time.time())
            _log.info("FX chart cached for %s/%s (%d history points)", base, target, len(prices))
        return file_id
    except Exception as exc:
        _log.exception("FX chart build failed for %s/%s: %s", base, target, exc)
        return None
    finally:
        _fx_chart_in_progress.pop(key, None)


async def _user_lang_and_currency(telegram_id: int) -> tuple[str, str]:
    """Returns (language, base_currency_code) for the inline-query user."""
    from app.i18n import get_user_lang
    from app.db.models import User
    from sqlalchemy import select

    async with SessionLocal() as db:
        lang = await get_user_lang(db, telegram_id)
        row = (await db.execute(
            select(User.base_currency).where(User.telegram_id == telegram_id).limit(1)
        )).scalar_one_or_none()
        ccy = row.value if row else "USD"
    return lang, ccy


async def render_main_menu(
    *,
    bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
    force_new: bool = False,
) -> None:
    """Yona-branded top-level menu — shown on /start and via `menu:home`."""
    from app.i18n import t as _t
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = getattr(user, "language", "ru") or "ru"
        wallets = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=panel_user_id,
        text=_t("menu.yona.text", lang),
        reply_markup=yona_main_menu_keyboard(wallets_count=len(wallets), lang=lang),
        parse_mode="HTML",
        disable_web_preview=True,
        force_new=force_new,
    )


@router.message(CommandStart())
@router.message(Command("start"))
async def start(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    uid = message.from_user.id
    chat_id = message.chat.id

    if uid in _start_in_flight:
        return
    _start_in_flight.add(uid)
    try:
        # First-time visitors get a wallet auto-created so the menu is never empty.
        try:
            async with SessionLocal() as db:
                user = await ledger.ensure_user(db, uid, message.from_user.username)
                await _ensure_first_wallet(db, user.id)
        except Exception:
            pass
        await render_main_menu(
            bot=message.bot,
            chat_id=chat_id,
            panel_user_id=uid,
            telegram_id=uid,
            username=message.from_user.username,
            force_new=True,
        )
    finally:
        _start_in_flight.discard(uid)


@router.message(Command("test"))
async def test_command(message: Message) -> None:
    """Legacy /start behaviour — full analytics dashboard."""
    if not message.from_user or not message.bot:
        return
    uid = message.from_user.id
    chat_id = message.chat.id

    if uid in _start_in_flight:
        return
    _start_in_flight.add(uid)
    try:
        from app.i18n import t as _t, get_user_lang as _get_lang
        try:
            async with SessionLocal() as _db:
                _lang = await _get_lang(_db, uid)
        except Exception:
            _lang = "ru"
        try:
            await push_text_panel(
                bot=message.bot,
                chat_id=chat_id,
                user_id=uid,
                text=_t("loading", _lang),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]),
                parse_mode=None,
            )
        except Exception:
            pass

        await render_profile(
            bot=message.bot,
            chat_id=chat_id,
            panel_user_id=uid,
            telegram_id=uid,
            username=message.from_user.username,
            period="week",
        )
    finally:
        _start_in_flight.discard(uid)


@router.callback_query(F.data.startswith("menu:"))
async def menu_action(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return
    action = callback.data.split(":", 1)[1]
    await callback.answer()
    bot = callback.bot
    chat_id = callback.message.chat.id
    uid = callback.from_user.id
    uname = callback.from_user.username

    if action in ("home", "profile"):
        await render_main_menu(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
    elif action == "wallets":
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
    elif action in ("p2p", "checks", "invoices", "subs", "refs"):
        from app.i18n import get_user_lang as _gl, t as _t
        async with SessionLocal() as _db:
            _lang = await _gl(_db, uid)
        try:
            await callback.answer(_t("in_development", _lang), show_alert=True)
        except TelegramBadRequest:
            pass
        return
    elif action == "history":
        filters = HistoryFilters()
        _user_history_filters[uid] = filters
        await render_history(
            bot=bot,
            chat_id=chat_id,
            panel_user_id=uid,
            telegram_id=uid,
            username=uname,
            filters=filters,
            back_to="menu:home",
            lock_source=False,
        )
    elif action == "integrations":
        from app.i18n import get_user_lang, t as _t
        async with SessionLocal() as _db:
            _lang = await get_user_lang(_db, uid)
        await render_integrations_menu(
            bot=bot,
            chat_id=chat_id,
            telegram_id=uid,
            username=uname,
            text=_t("tx.integrations_intro", _lang),
        )
    elif action == "settings":
        from app.bot.handlers.settings import render_settings_menu
        await render_settings_menu(
            bot=bot, chat_id=chat_id, telegram_id=uid, username=uname,
        )


@router.message(Command("add"))
@router.message(F.text.regexp(r"^[+-]"))
async def add_transaction(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    from app.i18n import get_user_lang, t as _t
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
        source_text = message.text.replace("/add", "", 1).strip()
        try:
            tx = await ledger.add_manual_transaction(db, user.id, source_text)
        except ValueError as exc:
            await push_text_panel(
                bot=message.bot,
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                text=_t("tx.invalid_input", lang, err=str(exc)),
                reply_markup=back_home_keyboard(lang),
                parse_mode=None,
            )
            return
        await push_text_panel(
            bot=message.bot,
            chat_id=message.chat.id,
            user_id=message.from_user.id,
            text=_t(
                "tx.saved", lang,
                tx_type=tx.tx_type.value,
                amount=tx.amount,
                currency=tx.currency.value,
                category=tx.category,
                desc=tx.description,
            ),
            reply_markup=back_home_keyboard(lang),
            parse_mode=None,
        )


@router.inline_query()
async def inline_query_handler(inline_query: InlineQuery) -> None:
    if not inline_query.from_user:
        return
    from app.i18n import t as _t

    raw = (inline_query.query or "").strip()
    uid = inline_query.from_user.id
    uname = inline_query.from_user.username
    lang, default_ccy = await _user_lang_and_currency(uid)

    # Empty query: offer full text profile sharing.
    if not raw:
        profile_text = await build_profile_text_for_inline(
            bot=inline_query.bot,
            telegram_id=uid,
            username=uname,
        )
        result = InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=_t("inline.share_profile", lang),
            description=_t("inline.share_profile_desc", lang),
            input_message_content=InputTextMessageContent(
                message_text=profile_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
        )
        await inline_query.answer([result], cache_time=1, is_personal=True)
        return

    parsed = _parse_inline_query(raw)
    if not parsed:
        return
    amount, base, target = parsed

    text = await _build_conversion_text(
        amount, base, target,
        default_target=default_ccy, lang=lang,
    )
    if not text:
        return

    keyboard = _fx_refresh_keyboard(amount, base, target, lang=lang)
    effective_target = target or default_ccy
    title = f"{_fmt_main(amount)} {base} → {effective_target}"

    # Try to deliver a chart on the FIRST query: wait up to 4s for background
    # generation. If it completes in time → photo; if not → text article and
    # the task keeps running so the next inline query gets the cached photo.
    # `shield` prevents the wait_for timeout from cancelling the in-flight task.
    chart_file_id = _cached_fx_file_id(base, effective_target, lang)
    if chart_file_id is None:
        bg_task = asyncio.create_task(_build_and_cache_fx_chart(
            inline_query.bot, base, effective_target, lang,
        ))
        try:
            chart_file_id = await asyncio.wait_for(
                asyncio.shield(bg_task), timeout=4.0,
            )
        except asyncio.TimeoutError:
            chart_file_id = None

    if chart_file_id:
        # Photo captions are limited to 1024 chars — fits comfortably here.
        result = InlineQueryResultCachedPhoto(
            id=str(uuid.uuid4()),
            photo_file_id=chart_file_id,
            title=title,
            description=_t("inline.conversion_desc", lang),
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
    else:
        result = InlineQueryResultArticle(
            id=str(uuid.uuid4()),
            title=title,
            description=_t("inline.conversion_desc", lang),
            input_message_content=InputTextMessageContent(
                message_text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            ),
            reply_markup=keyboard,
        )
    await inline_query.answer([result], cache_time=1, is_personal=True)


@router.callback_query(F.data.startswith("fxupd:"))
async def fx_refresh_callback(callback: CallbackQuery) -> None:
    """Silently refreshes the inline conversion message. Never shows popups/toasts."""
    # Always dismiss the loading spinner without text.
    async def _silent_dismiss() -> None:
        try:
            await callback.answer()
        except Exception:
            pass

    if not callback.data or not callback.from_user:
        await _silent_dismiss()
        return
    parts = callback.data.split(":", 3)
    if len(parts) != 4:
        await _silent_dismiss()
        return
    _, amt_str, base, target_str = parts
    target = target_str if target_str != "-" else None

    # Per-user rate limit (silent — no popup either way).
    uid = callback.from_user.id
    now = _time.time()
    last = _last_fx_refresh.get(uid, 0.0)
    if now - last < _FX_REFRESH_COOLDOWN:
        await _silent_dismiss()
        return

    try:
        amount = Decimal(amt_str)
    except Exception:
        await _silent_dismiss()
        return

    # Refresh button must actually re-fetch — invalidate every cache that could
    # otherwise serve a stale number.
    global _er_rates_cache, _ton_pct_cache
    _er_rates_cache = None
    _ton_pct_cache = None
    for sym in (base, target):
        if not sym:
            continue
        upper = sym.upper()
        _cg_rate_cache.pop(upper, None)
        # Wipe history caches for this ticker so the chart redraws fresh.
        for key in list(_cg_history_cache.keys()):
            if key[0] == upper:
                _cg_history_cache.pop(key, None)
        for key in list(_FIAT_HISTORY_CACHE.keys()):
            if key[0].upper() == upper:
                _FIAT_HISTORY_CACHE.pop(key, None)
    # Forget the rendered file_id for this pair so the next inline query
    # rebuilds the card with fresh numbers.
    for key in list(_fx_chart_file_cache.keys()):
        if key[0] == base.upper() and (target is None or key[1] == target.upper()):
            _fx_chart_file_cache.pop(key, None)

    lang, default_ccy = await _user_lang_and_currency(uid)
    text = await _build_conversion_text(
        amount, base, target,
        default_target=default_ccy, lang=lang,
    )
    if not text:
        await _silent_dismiss()
        return

    _last_fx_refresh[uid] = now
    keyboard = _fx_refresh_keyboard(amount, base, target, lang=lang)
    bot = callback.bot

    async def _try_edit_caption() -> bool:
        try:
            if callback.inline_message_id:
                await bot.edit_message_caption(
                    inline_message_id=callback.inline_message_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            elif callback.message:
                await bot.edit_message_caption(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
            return True
        except TelegramBadRequest:
            return False
        except Exception:
            return False

    async def _try_edit_text() -> bool:
        try:
            if callback.inline_message_id:
                await bot.edit_message_text(
                    text=text,
                    inline_message_id=callback.inline_message_id,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            elif callback.message:
                await bot.edit_message_text(
                    text=text,
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
            return True
        except TelegramBadRequest:
            return False
        except Exception:
            return False

    # Photo messages need edit_message_caption; plain text messages need
    # edit_message_text. We don't know upfront which one was sent so try both.
    if not await _try_edit_caption():
        await _try_edit_text()
    await _silent_dismiss()
