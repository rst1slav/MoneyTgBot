from decimal import Decimal
from time import time

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import history_keyboard
from app.bot.panel import push_photo_panel, push_text_panel
from app.db.models import AccountType, Currency, Transaction, TransactionType
from app.db.session import SessionLocal
from app.i18n import t
from app.services.history_service import HistoryFilters, HistoryService
from app.services.ledger_service import LedgerService
from app.services.monobank_service import MonobankService
from app.services.report_service import ReportService
from app.services.ton_service import TonService

router = Router(name="history")
ledger = LedgerService()
history_service = HistoryService()
monobank_service = MonobankService()
ton_service = TonService()
report_service = ReportService()

MAX_HISTORY_LEN = 3900
MAX_PHOTO_CAPTION = 1000  # Telegram caps photo captions at ~1024
PER_PAGE = 10

# Cooldown for external syncs to avoid rate limits (60 seconds)
_last_sync_time: dict[tuple[int, str], float] = {}
SYNC_COOLDOWN = 60

# Keep track of active filters and page per user in memory
_user_history_filters: dict[int, HistoryFilters] = {}
_user_history_page: dict[int, int] = {}
_user_history_back: dict[int, str] = {}      # back-callback for current history view
_user_history_locked: dict[int, bool] = {}   # whether source-filter row is hidden


def _parse_history_filters(text: str) -> HistoryFilters:
    filters = HistoryFilters()
    if "expenses" in text:
        filters.tx_type = TransactionType.EXPENSE
    if "income" in text:
        filters.tx_type = TransactionType.INCOME
    if "manual" in text:
        filters.account_type = AccountType.MANUAL
    if "card" in text:
        filters.account_type = AccountType.MONOBANK_CARD
    if "wallet" in text:
        filters.account_type = AccountType.TON_WALLET
    if "min=" in text:
        try:
            value = text.split("min=", 1)[1].split()[0]
            filters.min_amount = Decimal(value)
        except Exception:
            pass
    if "category=" in text:
        filters.category = text.split("category=", 1)[1].split()[0]
    return filters


_DESC_MAX = 30  # truncate transaction descriptions for compactness


def _history_body(txs: list[Transaction], lang: str = "ru") -> str:
    """
    Single-line format per transaction (category lives on the pie chart):
      🟢 09.05.26 14:30 | 100.00 UAH | Magnit
      🔴 09.05.26 12:15 | 50.00 UAH | Old Town

    Crypto rows (TON, jettons) drop the trailing description and use an em-dash:
      🟢 09.05.26 14:30 — 0.5 TON
    """
    if not txs:
        return t("history.empty", lang)
    rows = []
    for tx in txs:
        icon = '🟢' if tx.tx_type.value == 'income' else '🔴'
        is_crypto = tx.currency == Currency.TON
        precision = 4 if is_crypto else 2
        amount_str = f"{tx.amount:.{precision}f}"
        date_str = tx.created_at.strftime("%d.%m.%y %H:%M")
        currency = tx.currency.value

        if is_crypto:
            line = f"{icon} {date_str} — {amount_str} {currency}"
        else:
            line = f"{icon} {date_str} | {amount_str} {currency}"
            if tx.description:
                desc = tx.description.strip()
                if len(desc) > _DESC_MAX:
                    desc = desc[: _DESC_MAX - 1] + "…"
                line = f"{line} | {desc}"
        rows.append(line)
    return "\n".join(rows)


async def render_history(
    *,
    bot: Bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
    filters: HistoryFilters,
    page: int = 1,
    back_to: str | None = None,
    lock_source: bool | None = None,
) -> None:
    # Persist context so subsequent filter clicks reuse it.
    if back_to is not None:
        _user_history_back[panel_user_id] = back_to
    if lock_source is not None:
        _user_history_locked[panel_user_id] = lock_source

    effective_back = _user_history_back.get(panel_user_id, "menu:home")
    effective_lock = _user_history_locked.get(panel_user_id, False)

    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = getattr(user, "language", "ru") or "ru"
        now = time()

        # Skip mono sync entirely when viewing crypto-only context.
        relevant_mono = (
            filters.account_type in (None, AccountType.MONOBANK_CARD)
            and filters.account_id is None  # account_id-locked views are synced via that account's source
        )
        if relevant_mono:
            last_mono = _last_sync_time.get((user.id, "mono"), 0.0)
            if (now - last_mono) > SYNC_COOLDOWN:
                mono_acc = await monobank_service.get_active_account(db, user.id)
                if mono_acc:
                    await monobank_service.sync_transactions(db, mono_acc)
                    _last_sync_time[(user.id, "mono")] = now

        relevant_ton = filters.account_type in (None, AccountType.TON_WALLET) and filters.account_id is None
        if relevant_ton:
            last_ton = _last_sync_time.get((user.id, "ton"), 0.0)
            if (now - last_ton) > SYNC_COOLDOWN:
                ton_acc = await ton_service.get_active_account(db, user.id)
                if ton_acc:
                    await ton_service.sync_transactions(db, ton_acc)
                    _last_sync_time[(user.id, "ton")] = now

        # If account_id is locked, sync that specific account.
        if filters.account_id is not None:
            account = await ledger.get_account_by_id(db, user.id, filters.account_id)
            if account:
                if account.account_type == AccountType.MONOBANK_CARD:
                    await monobank_service.sync_transactions(db, account)
                elif account.account_type == AccountType.TON_WALLET:
                    await ton_service.sync_transactions(db, account)

        # Fetch one extra row to detect whether the next page would have anything.
        rows = await history_service.get_transactions(
            db, user.id, filters, page=page, per_page=PER_PAGE + 1
        )
        has_next = len(rows) > PER_PAGE
        txs = rows[:PER_PAGE]
        try:
            total_count = await history_service.count_transactions(db, user.id, filters)
        except Exception:
            total_count = page * PER_PAGE + (1 if has_next else 0)
        total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)
        chart_path = None
        try:
            chart_path = await report_service.generate_history_pie_chart(
                db, user.id,
                tx_type=filters.tx_type,
                account_type=filters.account_type,
                account_id=filters.account_id,
            )
        except Exception:
            chart_path = None

    body = _history_body(txs, lang)
    keyboard = history_keyboard(
        filters,
        page=page,
        back_to=effective_back,
        lock_source=effective_lock,
        has_prev=page > 1,
        has_next=has_next,
        total_pages=total_pages,
        lang=lang,
    )
    truncated_suffix = f"\n{t('history.truncated', lang)}"

    if chart_path:
        # Photo captions are capped — truncate body to fit comfortably.
        caption = (
            body if len(body) <= MAX_PHOTO_CAPTION
            else body[: MAX_PHOTO_CAPTION - len(truncated_suffix)] + truncated_suffix
        )
        await push_photo_panel(
            bot=bot,
            chat_id=chat_id,
            user_id=panel_user_id,
            photo_path=str(chart_path),
            caption=caption,
            reply_markup=keyboard,
            parse_mode=None,
        )
    else:
        if len(body) > MAX_HISTORY_LEN:
            body = body[: MAX_HISTORY_LEN - len(truncated_suffix)] + truncated_suffix
        await push_text_panel(
            bot=bot,
            chat_id=chat_id,
            user_id=panel_user_id,
            text=body,
            reply_markup=keyboard,
            parse_mode=None,
        )


@router.message(Command("history"))
async def history(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    text = message.text or ""
    filters = _parse_history_filters(text)
    user_id = message.from_user.id
    _user_history_filters[user_id] = filters
    _user_history_page[user_id] = 1
    await render_history(
        bot=message.bot,
        chat_id=message.chat.id,
        panel_user_id=user_id,
        telegram_id=user_id,
        username=message.from_user.username,
        filters=filters,
        page=1,
    )


@router.callback_query(F.data.startswith("history:"))
async def history_filter_selected(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return
    key = callback.data.split(":", 1)[1]
    
    # Get current filters for user or create new
    user_id = callback.from_user.id
    filters = _user_history_filters.get(user_id, HistoryFilters())
    page = _user_history_page.get(user_id, 1)
    
    if key == "noop":
        await callback.answer()
        return

    if key.startswith("page_"):
        try:
            page = int(key.split("_")[1])
        except (ValueError, IndexError):
            page = 1
    elif key == "type_all":
        filters.tx_type = None
        page = 1
    elif key == "type_expense":
        filters.tx_type = TransactionType.EXPENSE
        page = 1
    elif key == "type_income":
        filters.tx_type = TransactionType.INCOME
        page = 1
    elif key == "source_all":
        filters.account_type = None
        filters.account_id = None
        _user_history_locked[user_id] = False
        page = 1
    elif key == "source_manual":
        filters.account_type = AccountType.MANUAL
        filters.account_id = None
        _user_history_locked[user_id] = False
        page = 1
    elif key == "source_card":
        filters.account_type = AccountType.MONOBANK_CARD
        filters.account_id = None
        _user_history_locked[user_id] = False
        page = 1
    elif key == "source_crypto":
        filters.account_type = AccountType.TON_WALLET
        filters.account_id = None
        _user_history_locked[user_id] = False
        page = 1
    elif key == "amt_all":
        filters.size = None
        filters.min_amount = None
        filters.max_amount = None
        page = 1
    elif key == "amt_big":
        filters.size = "big"
        filters.min_amount = None
        filters.max_amount = None
        page = 1
    elif key == "amt_small":
        filters.size = "small"
        filters.min_amount = None
        filters.max_amount = None
        page = 1
    
    _user_history_filters[user_id] = filters
    _user_history_page[user_id] = page
    await callback.answer()
    await render_history(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        panel_user_id=callback.from_user.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        filters=filters,
        page=page,
    )
