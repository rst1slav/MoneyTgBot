import html as _html
import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.models import (
    Account, AccountType, Currency, Transaction, TransactionType, User,
)
from app.db.session import SessionLocal
from app.services.fx_service import FxService
from app.services.monobank_service import MonobankService
from app.services.report_service import ReportService
from app.services.ton_service import TonService

log = logging.getLogger(__name__)

fx_service = FxService()
report_service = ReportService()
mono_service = MonobankService()
ton_service = TonService()

_bot_ref: Bot | None = None


def _shorten_addr(s: str | None) -> str:
    if not s:
        return "..."
    if len(s) <= 12:
        return s
    return f"{s[:5]}...{s[-4:]}"


async def _notify_new_incomes(bot: Bot) -> None:
    """
    Шлёт юзеру уведомление о каждом новом income-transaction (notified=False),
    после успешной отправки помечает notified=True. Конвертация суммы в
    базовую валюту юзера — через FxService + ton_price_usd.
    """
    from app.i18n import t
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(Transaction, Account, User)
                .join(Account, Account.id == Transaction.account_id)
                .join(User, User.id == Transaction.user_id)
                .where(
                    Transaction.tx_type == TransactionType.INCOME,
                    Transaction.notified.is_(False),
                    Account.account_type == AccountType.TON_WALLET,
                )
                .order_by(Transaction.created_at.asc())
                .limit(50)
            )
        ).all()
        if not rows:
            return

        # Один курс для всех уведомлений в батче, чтобы не дёргать API на каждое.
        try:
            uah_per_usd = await fx_service.latest_uah_per_usd(db)
        except Exception:
            uah_per_usd = None
        try:
            ton_price_usd = await ton_service.ton_price_usd()
        except Exception:
            ton_price_usd = None

        for tx, account, user in rows:
            try:
                base_ccy = user.base_currency.value if user.base_currency else "UAH"
                amount = Decimal(tx.amount or 0)
                # Считаем USD-эквивалент.
                if tx.currency == Currency.TON and ton_price_usd:
                    usd_value = amount * ton_price_usd
                elif tx.currency == Currency.USDT:
                    usd_value = amount  # стейбл 1:1
                else:
                    usd_value = None

                # Конвертация в базовую валюту юзера для отображения.
                base_amount: Decimal | None = None
                base_label = base_ccy
                if base_ccy == "UAH" and usd_value is not None and uah_per_usd:
                    base_amount = usd_value * uah_per_usd
                elif base_ccy == "USD" and usd_value is not None:
                    base_amount = usd_value
                elif usd_value is not None:
                    try:
                        from app.bot.handlers.profile import _get_base_per_usd
                        base_per_usd = await _get_base_per_usd(base_ccy)
                        if base_per_usd is not None:
                            base_amount = usd_value * base_per_usd
                    except Exception:
                        base_amount = None

                from app.bot.handlers.profile import (
                    _format_coin_amount, _ton_display_label,
                )
                amt_str = _format_coin_amount(amount)
                coin_sym = tx.currency.value
                wallet_label = _ton_display_label(account)
                short_addr = _shorten_addr(account.external_ref)

                if base_amount is not None:
                    money_part = (
                        f"<b>{amt_str} {coin_sym} "
                        f"({base_amount:.2f} {base_label})</b>"
                    )
                else:
                    money_part = f"<b>{amt_str} {coin_sym}</b>"

                # external_tx_id = "{event_id}#{action_idx}". На слове
                # «получили» — ссылка на транзу, на адресе — ссылка на
                # сам адрес. Это разные страницы tonviewer.
                event_id = (tx.external_tx_id or "").split("#", 1)[0]
                tx_url = (
                    f"https://tonviewer.com/transaction/{event_id}"
                    if event_id else None
                )
                addr_url = (
                    f"https://tonviewer.com/{account.external_ref}"
                    if account.external_ref else None
                )
                received_word = (
                    f'<a href="{tx_url}">получили</a>'
                    if tx_url else "получили"
                )
                addr_part = (
                    f'<a href="{addr_url}">{_html.escape(short_addr)}</a>'
                    if addr_url
                    else f"<code>{_html.escape(short_addr)}</code>"
                )

                text = (
                    f"📥 Вы {received_word} {money_part} на "
                    f"{_html.escape(wallet_label)} "
                    f"({addr_part})."
                )
                # Если в транзе был комментарий — приклеиваем строку
                # «💬 …» после пустой строки. Дефолтные плейсхолдеры
                # из ton_service._parse_action в качестве memo не считаем.
                desc = (tx.description or "").strip()
                if desc and desc not in {
                    "ton transfer", "USDT transfer", "USDC transfer",
                    "NOT transfer", "USD₮ transfer",
                }:
                    text += f"\n\n💬 {_html.escape(desc)}"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="👛 Открыть кошелёк",
                        callback_data=f"crypto:open_wallet:{account.id}",
                    ),
                ]])
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
                tx.notified = True
            except Exception as exc:
                log.warning(
                    "deposit notify failed for tx_id=%s: %s", tx.id, exc,
                )
        await db.commit()


async def sync_external_accounts() -> None:
    async with SessionLocal() as db:
        accounts = (
            await db.execute(
                select(Account).where(
                    Account.is_active.is_(True),
                    Account.account_type.in_([AccountType.MONOBANK_CARD, AccountType.TON_WALLET]),
                )
            )
        ).scalars().all()
        for account in accounts:
            try:
                if account.account_type == AccountType.MONOBANK_CARD:
                    await mono_service.sync_transactions(db, account)
                elif account.account_type == AccountType.TON_WALLET:
                    await ton_service.sync_transactions(db, account)
            except Exception:
                continue
    # После каждой синхронизации — пуш уведомлений по новым income.
    if _bot_ref is not None:
        try:
            await _notify_new_incomes(_bot_ref)
        except Exception as exc:
            log.warning("notify_new_incomes failed: %s", exc)


async def generate_daily_reports() -> None:
    async with SessionLocal() as db:
        users = (await db.execute(select(User))).scalars().all()
        for user in users:
            try:
                await report_service.generate_profile_chart(db, user.id, period="week")
            except Exception:
                continue


async def update_fx_rates() -> None:
    async with SessionLocal() as db:
        try:
            await fx_service.refresh_uah_usd(db)
        except Exception:
            return


# Популярные пары — пре-генерим их в фоне, чтобы первый инлайн-запрос юзера
# отдавался мгновенно из кэша.
_POPULAR_PAIRS: list[tuple[str, str]] = [
    ("TON", "USD"), ("TON", "USDT"), ("TON", "RUB"), ("TON", "UAH"),
    ("BTC", "USD"), ("ETH", "USD"),
    ("USD", "RUB"), ("USD", "UAH"), ("USD", "EUR"),
    ("EUR", "USD"),
]
_POPULAR_PERIODS = (1, 7, 30)
_POPULAR_LANGS = ("ru", "en", "uk")


async def prewarm_fx_charts() -> None:
    """
    Раз в N минут — фоновая пред-генерация rate-карточек для популярных пар
    по всем периодам и языкам. Чтобы кэш был всегда тёплый.
    """
    # Импорт здесь чтобы не плодить циклы: handlers/transactions импортит scheduler.
    try:
        from app.bot.handlers.transactions import _build_and_cache_fx_chart
    except Exception:
        return
    for base, target in _POPULAR_PAIRS:
        for period in _POPULAR_PERIODS:
            for lang in _POPULAR_LANGS:
                try:
                    await _build_and_cache_fx_chart(None, base, target, lang, period)
                except Exception:
                    continue


def create_scheduler(*, bot: Bot | None = None) -> AsyncIOScheduler:
    global _bot_ref
    _bot_ref = bot
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_fx_rates, "cron", hour=3, minute=0)
    # Депозиты должны прилетать в чат быстро — крутим синк раз в минуту.
    scheduler.add_job(sync_external_accounts, "interval", minutes=1)
    scheduler.add_job(generate_daily_reports, "cron", hour=4, minute=0)
    # Пре-генерация популярных карточек — каждые 25 минут (TTL кэша 30 мин).
    scheduler.add_job(prewarm_fx_charts, "interval", minutes=25)
    return scheduler
