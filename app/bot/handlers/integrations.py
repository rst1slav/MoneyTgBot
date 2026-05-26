import re

import httpx
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import and_, case, func, select

from app.bot.keyboards import (
    integrations_account_keyboard,
    integrations_bank_keyboard,
    integrations_categories_keyboard,
    integrations_pending_keyboard,
    integrations_rename_keyboard,
    integrations_unlink_confirm_keyboard,
)
from app.bot.panel import push_photo_panel, push_text_panel
from app.db.models import Account, AccountType, Transaction, TransactionType
from app.db.session import SessionLocal
from app.i18n import get_user_lang, t
from app.services.history_service import HistoryFilters
from app.services.ledger_service import LedgerService
from app.services.monobank_service import MonobankService
from app.services.report_service import ReportService
from app.services.ton_service import TonService

router = Router(name="integrations")
ledger = LedgerService()
monobank_service = MonobankService()
ton_service = TonService()
report_service = ReportService()

# Pending input states keyed by telegram user id.
_pending_link: dict[int, str] = {}        # → bank_slug
_pending_rename: dict[int, int] = {}      # → account_id


# slug → (label, AccountType, default_currency)
BANK_DEFS: dict[str, tuple[str, AccountType, str]] = {
    "monobank": ("Monobank", AccountType.MONOBANK_CARD, "UAH"),
    "ton":      ("TON",      AccountType.TON_WALLET,    "TON"),
}

MAX_SLOTS = 5  # max concurrent connections per integration

DEFAULT_DISPLAY_NAMES = {"Monobank card", "TON wallet"}

# TON address formats:
#  - Raw:        workchain ':' + 64 hex chars  (e.g. 0:abcd…)
#  - Friendly:   48 base64url chars             (EQ.../UQ.../kQ.../etc.)
_TON_RAW_RE = re.compile(r"^-?\d+:[a-fA-F0-9]{64}$")
_TON_FRIENDLY_RE = re.compile(r"^[A-Za-z0-9_-]{48}$")
# Monobank tokens are at least ~40 chars of base64-ish characters.
_MONOBANK_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{30,}$")
# A masked-PAN auto-label looks like "1234...5678" — treat that as auto, not a custom name.
_AUTO_PAN_RE = re.compile(r"^\d{4}\.\.\.\d{4}$")
# Stale shortened-ref pattern: e.g. "VsMOl...sNg3" — has letters, NOT a real PAN.
_STALE_REF_RE = re.compile(r"^[A-Za-z0-9]{3,}\.\.\.[A-Za-z0-9]{3,}$")


def _shorten_ref(ref: str | None) -> str:
    if not ref:
        return "..."
    if len(ref) <= 12:
        return ref
    return f"{ref[:5]}...{ref[-4:]}"


def _is_default_label(name: str | None) -> bool:
    """True if display_name is auto-generated (legacy default, masked PAN, or stale ref)."""
    if not name:
        return True
    if name in DEFAULT_DISPLAY_NAMES:
        return True
    if _AUTO_PAN_RE.match(name):
        return True
    if _STALE_REF_RE.match(name):
        return True
    return False


def _has_custom_name(name: str | None) -> bool:
    return bool(name) and not _is_default_label(name)


def _bank_slug_for_type(acc_type: AccountType) -> str | None:
    for slug, (_, t, _) in BANK_DEFS.items():
        if t == acc_type:
            return slug
    return None


def _slot_label(account: Account, lang: str = "ru") -> str:
    name = account.display_name
    # 1. User-set custom name wins.
    if _has_custom_name(name):
        return name
    # 2. Auto masked PAN (e.g. "4441...5985") — keep as-is.
    if name and _AUTO_PAN_RE.match(name):
        return name
    # 3. Monobank account with no PAN known — generic label, never expose API ref.
    if account.account_type == AccountType.MONOBANK_CARD:
        return t("int.account_card", lang)
    # 4. TON wallet — external_ref IS the wallet address; safe to shorten.
    return _shorten_ref(account.external_ref)


def _build_slots(accounts: list[Account], lang: str = "ru") -> list[tuple[int | None, str]]:
    slots: list[tuple[int | None, str]] = []
    for acc in accounts[:MAX_SLOTS]:
        slots.append((acc.id, _slot_label(acc, lang)))
    while len(slots) < MAX_SLOTS:
        slots.append((None, ""))
    return slots


async def _validate_monobank(token: str, card_id: str, lang: str = "ru") -> tuple[bool, str]:
    """
    Returns (ok, error_message). Validates token format then verifies via Monobank API.
    """
    if not token:
        return False, t("int.empty_token", lang)
    if not _MONOBANK_TOKEN_RE.match(token):
        return (
            False,
            t("int.invalid_token_fmt", lang),
        )

    data = await monobank_service._fetch_client_info(token)
    if not data:
        return (
            False,
            t("int.token_not_working", lang),
        )

    if not card_id or card_id.lower() in {"auto", "first"}:
        return True, ""

    accounts = data.get("accounts", [])
    valid_ids = {str(a.get("id", "")) for a in accounts}
    valid_pans_digits = set()
    for a in accounts:
        for pan in a.get("maskedPan", []):
            valid_pans_digits.add("".join(c for c in str(pan) if c.isdigit()))

    if card_id in valid_ids:
        return True, ""

    hint_digits = "".join(c for c in card_id if c.isdigit())
    if hint_digits:
        for pan_digits in valid_pans_digits:
            if pan_digits.endswith(hint_digits) or hint_digits.endswith(pan_digits[-4:]):
                return True, ""

    return (
        False,
        t("int.card_not_found", lang, card=card_id),
    )


async def _validate_ton_wallet(address: str, lang: str = "ru") -> tuple[bool, str]:
    """
    Returns (ok, error_message). Validates address format then queries tonapi to
    confirm the account exists on-chain.
    """
    a = (address or "").strip()
    if not a:
        return False, t("int.empty_address", lang)
    if not (_TON_RAW_RE.match(a) or _TON_FRIENDLY_RE.match(a)):
        return (
            False,
            t("int.invalid_ton_addr", lang),
        )

    settings = ton_service.settings
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.ton_api_url}/blockchain/accounts/{a}")
            if resp.status_code in {400, 404}:
                return False, t("int.ton_not_found", lang)
    except Exception:
        # Network issue — let the user proceed; sync will retry later.
        pass
    return True, ""


async def _stats_row(db, *, user_id: int, account_type: AccountType | None = None, account_id: int | None = None):
    expense = func.sum(case((Transaction.tx_type == TransactionType.EXPENSE, Transaction.amount), else_=0))
    income = func.sum(case((Transaction.tx_type == TransactionType.INCOME, Transaction.amount), else_=0))
    cnt = func.count(Transaction.id)
    stmt = (
        select(income.label("inc"), expense.label("exp"), cnt.label("cnt"))
        .join(Account, Account.id == Transaction.account_id)
        .where(Transaction.user_id == user_id)
    )
    if account_type is not None:
        stmt = stmt.where(Account.account_type == account_type)
    if account_id is not None:
        stmt = stmt.where(Transaction.account_id == account_id)
    row = (await db.execute(stmt)).first()
    return float(row.inc or 0), float(row.exp or 0), int(row.cnt or 0)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

async def render_categories_menu(
    *, bot, chat_id: int, telegram_id: int, username: str | None = None
) -> None:
    """Top-level integrations menu: shows Monobank and Crypto with current slot counts."""
    counts: dict[str, int] = {}
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = await get_user_lang(db, telegram_id)
        for slug, (_, acc_type, _) in BANK_DEFS.items():
            accs = await ledger.get_active_accounts_by_type(db, user.id, acc_type)
            counts[slug] = len(accs)
    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=telegram_id,
        text=f"<b>{t('int.title', lang)}</b>\n{t('int.choose_section', lang)}",
        reply_markup=integrations_categories_keyboard(counts, lang=lang),
        parse_mode="HTML",
    )


async def render_bank_menu(
    *, bot, chat_id: int, telegram_id: int, username: str | None, bank_slug: str
) -> None:
    bank_def = BANK_DEFS.get(bank_slug)
    if not bank_def:
        return
    _, acc_type, _ = bank_def

    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = await get_user_lang(db, telegram_id)
        accounts = await ledger.get_active_accounts_by_type(db, user.id, acc_type)

        if bank_slug == "monobank":
            for acc in accounts:
                if not _has_custom_name(acc.display_name) and not (
                    acc.display_name and _AUTO_PAN_RE.match(acc.display_name)
                ):
                    try:
                        res = await monobank_service.get_live_balance(acc)
                    except Exception:
                        res = None
                    if res:
                        _, _, pan_label = res
                        if pan_label:
                            acc.display_name = pan_label
                            try:
                                await db.commit()
                            except Exception:
                                pass

        inc, exp, n = await _stats_row(db, user_id=user.id, account_type=acc_type)

    section_title = t("profile.monobank", lang) if bank_slug == "monobank" else t("profile.crypto", lang)
    stats_text = (
        f"<b>{section_title}</b>\n"
        f"{t('int.income', lang)}: {inc:.2f}\n"
        f"{t('int.expense', lang)}: {exp:.2f}\n"
        f"{t('int.operations', lang)}: {n}"
    )
    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=telegram_id,
        text=stats_text,
        reply_markup=integrations_bank_keyboard(
            bank_slug=bank_slug, slots=_build_slots(accounts, lang=lang), lang=lang,
        ),
        parse_mode="HTML",
    )


async def render_account_detail(
    *, bot, chat_id: int, telegram_id: int, username: str | None, account_id: int
) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = await get_user_lang(db, telegram_id)
        account = await ledger.get_account_by_id(db, user.id, account_id)
        if not account:
            await push_text_panel(
                bot=bot, chat_id=chat_id, user_id=telegram_id,
                text=t("int.account_not_found", lang),
                reply_markup=integrations_pending_keyboard(lang=lang),
                parse_mode=None,
            )
            return

        bank_slug = _bank_slug_for_type(account.account_type) or "monobank"

        if (
            account.account_type == AccountType.MONOBANK_CARD
            and not _has_custom_name(account.display_name)
            and not (account.display_name and _AUTO_PAN_RE.match(account.display_name))
        ):
            try:
                res = await monobank_service.get_live_balance(account)
            except Exception:
                res = None
            if res:
                _, _, pan_label = res
                if pan_label:
                    account.display_name = pan_label
                    try:
                        await db.commit()
                    except Exception:
                        pass

        title = _slot_label(account, lang)
        if account.account_type == AccountType.TON_WALLET:
            ref_line = _shorten_ref(account.external_ref)
        elif account.display_name and _AUTO_PAN_RE.match(account.display_name):
            ref_line = account.display_name
        else:
            ref_line = "—"
        inc, exp, n = await _stats_row(db, user_id=user.id, account_id=account_id)

        text = (
            f"<b>{title}</b>\n"
            f"📌 <code>{ref_line}</code>\n"
            f"{t('int.income', lang)}: {inc:.2f}\n"
            f"{t('int.expense', lang)}: {exp:.2f}\n"
            f"{t('int.operations', lang)}: {n}"
        )
        chart_path = await report_service.generate_account_pie_chart(db, user.id, account_id)

    has_custom = _has_custom_name(account.display_name)
    keyboard = integrations_account_keyboard(
        account_id, bank_slug, has_custom_name=has_custom, lang=lang,
    )
    if chart_path:
        await push_photo_panel(
            bot=bot, chat_id=chat_id, user_id=telegram_id,
            photo_path=str(chart_path),
            caption=text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=telegram_id,
            text=text + f"\n\n<i>{t('int.no_expenses_30d', lang)}</i>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


# Backward-compatible entry point used by transactions.menu_action.
async def render_integrations_menu(
    *, bot, chat_id: int, telegram_id: int, username: str | None, text: str | None = None
) -> None:
    await render_categories_menu(bot=bot, chat_id=chat_id, telegram_id=telegram_id)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("int:"))
async def integrations_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return
    bot = callback.bot
    chat_id = callback.message.chat.id
    uid = callback.from_user.id
    uname = callback.from_user.username
    parts = callback.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    async with SessionLocal() as _db:
        lang = await get_user_lang(_db, uid)

    if action == "cat_menu":
        await callback.answer()
        await render_categories_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname)
        return

    if action == "bank":
        await callback.answer()
        await render_bank_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname, bank_slug=arg)
        return

    if action == "slot":
        try:
            account_id = int(arg)
        except ValueError:
            await callback.answer(t("int.error", lang))
            return
        await callback.answer()
        await render_account_detail(
            bot=bot, chat_id=chat_id, telegram_id=uid, username=uname, account_id=account_id
        )
        return

    if action == "link":
        bank_def = BANK_DEFS.get(arg)
        if not bank_def:
            await callback.answer()
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            existing = await ledger.get_active_accounts_by_type(db, user.id, bank_def[1])
        if len(existing) >= MAX_SLOTS:
            await callback.answer(
                t("int.slot_limit", lang, n=MAX_SLOTS), show_alert=True
            )
            return
        _pending_link[uid] = arg
        await callback.answer()
        if arg == "monobank":
            prompt = t("int.mono_link_prompt2", lang)
        else:  # ton
            prompt = t("int.ton_link_prompt", lang)
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=prompt,
            reply_markup=integrations_pending_keyboard(back_to=f"int:bank:{arg}", lang=lang),
            parse_mode="HTML",
        )
        return

    if action == "rename":
        try:
            account_id = int(arg)
        except ValueError:
            return
        _pending_rename[uid] = account_id
        has_custom = False
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            account = await ledger.get_account_by_id(db, user.id, account_id)
            if account:
                has_custom = _has_custom_name(account.display_name)
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("int.rename_prompt", lang),
            reply_markup=integrations_rename_keyboard(
                account_id, has_custom_name=has_custom, lang=lang,
            ),
            parse_mode=None,
        )
        return

    if action == "rename_clear":
        try:
            account_id = int(arg)
        except ValueError:
            return
        internal_uid: int | None = None
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            internal_uid = user.id
            account = await ledger.get_account_by_id(db, user.id, account_id)
            if account:
                if account.account_type == AccountType.MONOBANK_CARD:
                    account.display_name = "Monobank card"
                elif account.account_type == AccountType.TON_WALLET:
                    account.display_name = "TON wallet"
                else:
                    account.display_name = ""
                await db.commit()
        if internal_uid is not None:
            from app.bot.handlers.profile import _profile_snapshot_cache
            _profile_snapshot_cache.pop(internal_uid, None)
        await callback.answer(t("saved", lang))
        await render_account_detail(
            bot=bot, chat_id=chat_id, telegram_id=uid, username=uname, account_id=account_id
        )
        return

    if action == "unlink_ask":
        try:
            account_id = int(arg)
        except ValueError:
            return
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("int.unlink_confirm", lang),
            reply_markup=integrations_unlink_confirm_keyboard(account_id, lang=lang),
            parse_mode=None,
        )
        return

    if action == "unlink_yes":
        try:
            account_id = int(arg)
        except ValueError:
            return
        bank_slug = None
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            account = await ledger.get_account_by_id(db, user.id, account_id)
            if account:
                account.is_active = False
                await db.commit()
                bank_slug = _bank_slug_for_type(account.account_type)
        await callback.answer(t("int.unlinked", lang))
        if bank_slug:
            await render_bank_menu(
                bot=bot, chat_id=chat_id, telegram_id=uid, username=uname, bank_slug=bank_slug
            )
        else:
            await render_categories_menu(bot=bot, chat_id=chat_id, telegram_id=uid)
        return

    if action == "hist_bank":
        bank_def = BANK_DEFS.get(arg)
        if not bank_def:
            return
        await callback.answer()
        from app.bot.handlers.history import _user_history_filters, render_history
        filters = HistoryFilters(account_type=bank_def[1])
        _user_history_filters[uid] = filters
        await render_history(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, filters=filters,
            back_to=f"int:bank:{arg}",
            lock_source=True,
        )
        return

    if action == "hist_acc":
        try:
            account_id = int(arg)
        except ValueError:
            return
        await callback.answer()
        from app.bot.handlers.history import _user_history_filters, render_history
        filters = HistoryFilters(account_id=account_id)
        _user_history_filters[uid] = filters
        await render_history(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, filters=filters,
            back_to=f"int:slot:{account_id}",
            lock_source=True,
        )
        return


# ---------------------------------------------------------------------------
# Free-text input — only fires when user is in pending state
# ---------------------------------------------------------------------------

@router.message(
    lambda m: bool(
        m.from_user
        and m.text
        and (m.from_user.id in _pending_link or m.from_user.id in _pending_rename)
    )
)
async def integration_text_input(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    uid = message.from_user.id
    uname = message.from_user.username
    raw = message.text.strip()

    # Rename takes priority — most specific state.
    if uid in _pending_rename:
        account_id = _pending_rename.pop(uid)
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            account = await ledger.get_account_by_id(db, user.id, account_id)
            if account:
                account.display_name = raw[:255]
                await db.commit()
        await render_account_detail(
            bot=message.bot, chat_id=message.chat.id,
            telegram_id=uid, username=uname, account_id=account_id,
        )
        return

    bank_slug = _pending_link.get(uid)
    if not bank_slug:
        return

    if bank_slug == "monobank":
        parts = raw.split(maxsplit=1)
        token = parts[0] if parts else ""
        card_id = parts[1].strip() if len(parts) > 1 else "auto"

        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        ok, err = await _validate_monobank(token, card_id, lang)
        if not ok:
            # Keep pending state so user can retry without re-clicking.
            await push_text_panel(
                bot=message.bot, chat_id=message.chat.id, user_id=uid,
                text=f"❌ {err}",
                reply_markup=integrations_pending_keyboard(back_to="int:bank:monobank", lang=lang),
                parse_mode=None,
            )
            return

        _pending_link.pop(uid, None)
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            try:
                await monobank_service.link_token(db, user.id, token, card_id)
            except Exception:
                await push_text_panel(
                    bot=message.bot, chat_id=message.chat.id, user_id=uid,
                    text=t("int.link_failed_card", lang),
                    reply_markup=integrations_pending_keyboard(back_to="int:bank:monobank", lang=lang),
                    parse_mode=None,
                )
                return
        await render_bank_menu(
            bot=message.bot, chat_id=message.chat.id,
            telegram_id=uid, username=uname, bank_slug="monobank",
        )
        return

    if bank_slug == "ton":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        ok, err = await _validate_ton_wallet(raw, lang)
        if not ok:
            await push_text_panel(
                bot=message.bot, chat_id=message.chat.id, user_id=uid,
                text=f"❌ {err}",
                reply_markup=integrations_pending_keyboard(back_to="int:bank:ton", lang=lang),
                parse_mode=None,
            )
            return

        _pending_link.pop(uid, None)
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            try:
                await ton_service.link_wallet(db, user.id, raw)
            except Exception:
                await push_text_panel(
                    bot=message.bot, chat_id=message.chat.id, user_id=uid,
                    text=t("int.link_failed_wallet", lang),
                    reply_markup=integrations_pending_keyboard(back_to="int:bank:ton", lang=lang),
                    parse_mode=None,
                )
                return
        await render_bank_menu(
            bot=message.bot, chat_id=message.chat.id,
            telegram_id=uid, username=uname, bank_slug="ton",
        )
