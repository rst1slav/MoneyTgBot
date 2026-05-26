"""Settings menu: timezone, language, base currency, support contact."""
import html
import re
import time as _time

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    integrations_pending_keyboard,
    settings_currency_keyboard,
    settings_keyboard,
    settings_language_keyboard,
    settings_timezone_keyboard,
)
from app.bot.panel import push_text_panel
from app.db.models import Currency
from app.db.session import SessionLocal
from app.i18n import t
from app.services.ledger_service import LedgerService

router = Router(name="settings")
ledger = LedgerService()

# uid → timestamp at which the prompt was shown. Entries older than the TTL are
# treated as expired so a forgotten "Свой..." click stops capturing chat input.
_pending_tz_input: dict[int, float] = {}
_TZ_PENDING_TTL = 120  # 2 minutes


def _is_tz_pending(uid: int) -> bool:
    ts = _pending_tz_input.get(uid)
    if ts is None:
        return False
    if _time.time() - ts > _TZ_PENDING_TTL:
        _pending_tz_input.pop(uid, None)
        return False
    return True


def _clear_tz_pending(uid: int) -> None:
    _pending_tz_input.pop(uid, None)

_LANG_LABELS = {"en": "🇬🇧 English", "ru": "🇷🇺 Русский", "uk": "🇺🇦 Українська"}

# IANA name like Europe/Kyiv, or UTC offset like +02:00 / -05:30 / +03.
_IANA_RE = re.compile(r"^[A-Za-z]+(?:/[A-Za-z_+-]+)+$")
_OFFSET_RE = re.compile(r"^[+-]\d{1,2}(:\d{2})?$")


async def render_settings_menu(*, bot, chat_id: int, telegram_id: int, username: str | None) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        tz = user.timezone or "Europe/Kyiv"
        lang_code = getattr(user, "language", "ru") or "ru"
        ccy = user.base_currency.value if user.base_currency else "UAH"
    text = (
        f"<b>{t('settings.title', lang_code)}</b>\n\n"
        f"🕒 {t('settings.timezone_label', lang_code)}: <code>{html.escape(tz)}</code>\n"
        f"🌐 {t('settings.language_label', lang_code)}: {_LANG_LABELS.get(lang_code, lang_code)}\n"
        f"💱 {t('settings.currency_label', lang_code)}: <code>{ccy}</code>"
    )
    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=telegram_id,
        text=text,
        reply_markup=settings_keyboard(lang_code),
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("set:"))
async def settings_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return
    bot = callback.bot
    chat_id = callback.message.chat.id
    uid = callback.from_user.id
    uname = callback.from_user.username
    parts = callback.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    # Any settings click (except the one that ASKS for tz input) means the user
    # navigated away — drop pending tz state so subsequent messages aren't
    # mis-interpreted as timezone input.
    if action != "tz_custom":
        _clear_tz_pending(uid)

    if action == "open":
        await callback.answer()
        await render_settings_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname)
        return

    if action == "tz":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            current = user.timezone
            lang = getattr(user, "language", "ru") or "ru"
        tz_show = html.escape(current or t("settings.tz_not_set", lang))
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=(
                f"<b>{t('settings.timezone', lang)}</b>\n"
                f"{t('settings.timezone_now', lang, tz=tz_show)}\n\n"
                f"{t('settings.tz_pick_hint', lang)}"
            ),
            reply_markup=settings_timezone_keyboard(current=current, lang=lang),
            parse_mode="HTML",
        )
        return

    if action == "tz_pick":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            user.timezone = arg
            await db.commit()
            lang = getattr(user, "language", "ru") or "ru"
        await callback.answer(t("saved", lang))
        await render_settings_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname)
        return

    if action == "tz_custom":
        _pending_tz_input[uid] = _time.time()
        async with SessionLocal() as db:
            u = await ledger.ensure_user(db, uid, uname)
            lang = getattr(u, "language", "ru") or "ru"
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("settings.tz_input_prompt", lang),
            reply_markup=integrations_pending_keyboard(back_to="set:tz", lang=lang),
            parse_mode="HTML",
        )
        return

    if action == "lang":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            current = getattr(user, "language", "ru")
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=f"<b>{t('settings.language', current or 'ru')}</b>",
            reply_markup=settings_language_keyboard(current=current, lang=current or "ru"),
            parse_mode="HTML",
        )
        return

    if action == "lang_pick":
        if arg not in {"en", "ru", "uk"}:
            await callback.answer()
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            user.language = arg
            await db.commit()
        await callback.answer(t("saved", arg))
        await render_settings_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname)
        return

    if action == "ccy":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            current = user.base_currency.value if user.base_currency else "UAH"
            lang = getattr(user, "language", "ru") or "ru"
        await callback.answer()
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=(
                f"<b>{t('settings.currency', lang)}</b>\n"
                f"{t('settings.currency_hint', lang)}"
            ),
            reply_markup=settings_currency_keyboard(current=current, lang=lang),
            parse_mode="HTML",
        )
        return

    if action == "ccy_pick":
        try:
            ccy = Currency(arg)
        except ValueError:
            await callback.answer()
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            user.base_currency = ccy
            await db.commit()
            lang = getattr(user, "language", "ru") or "ru"
        await callback.answer(t("saved", lang))
        await render_settings_menu(bot=bot, chat_id=chat_id, telegram_id=uid, username=uname)
        return


@router.message(
    lambda m: bool(m.from_user and m.text and _is_tz_pending(m.from_user.id))
)
async def tz_text_input(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    uid = message.from_user.id
    uname = message.from_user.username
    raw = message.text.strip()

    if not (_IANA_RE.match(raw) or _OFFSET_RE.match(raw)):
        async with SessionLocal() as db:
            u = await ledger.ensure_user(db, uid, uname)
            lang = getattr(u, "language", "ru") or "ru"
        await push_text_panel(
            bot=message.bot, chat_id=message.chat.id, user_id=uid,
            text=t("settings.tz_invalid", lang),
            reply_markup=integrations_pending_keyboard(back_to="set:tz", lang=lang),
            parse_mode="HTML",
        )
        return

    _clear_tz_pending(uid)
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        user.timezone = raw[:64]
        await db.commit()
    await render_settings_menu(
        bot=message.bot, chat_id=message.chat.id,
        telegram_id=uid, username=uname,
    )
