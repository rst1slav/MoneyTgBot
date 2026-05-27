from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.models import Account, AccountType, User
from app.db.session import SessionLocal
from app.services.fx_service import FxService
from app.services.monobank_service import MonobankService
from app.services.report_service import ReportService
from app.services.ton_service import TonService

fx_service = FxService()
report_service = ReportService()
mono_service = MonobankService()
ton_service = TonService()


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


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(update_fx_rates, "cron", hour=3, minute=0)
    scheduler.add_job(sync_external_accounts, "interval", minutes=30)
    scheduler.add_job(generate_daily_reports, "cron", hour=4, minute=0)
    # Пре-генерация популярных карточек — каждые 25 минут (TTL кэша 30 мин).
    scheduler.add_job(prewarm_fx_charts, "interval", minutes=25)
    return scheduler
