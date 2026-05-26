"""Single-message UI panel per (chat_id, user_id): edit in place, avoid spam."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message

_panel_message_id: dict[tuple[int, int], int] = {}


def get_panel_id(chat_id: int, user_id: int) -> int | None:
    return _panel_message_id.get((chat_id, user_id))


def set_panel_id(chat_id: int, user_id: int, message_id: int) -> None:
    _panel_message_id[(chat_id, user_id)] = message_id


async def _safe_edit_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
    disable_web_preview: bool = False,
) -> None:
    try:
        await bot.edit_message_text(
            text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_preview,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def _safe_edit_media(
    bot: Bot,
    chat_id: int,
    message_id: int,
    media: InputMediaPhoto,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = "Markdown",
) -> None:
    try:
        if parse_mode:
            media.parse_mode = parse_mode
        await bot.edit_message_media(
            media=media,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        raise


async def show_text_panel(
    message: Message,
    *,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str | None = "Markdown",
) -> None:
    if not message.from_user or not message.bot:
        return
    bot = message.bot
    chat_id = message.chat.id
    user_id = message.from_user.id
    mid = get_panel_id(chat_id, user_id)
    if mid:
        try:
            await _safe_edit_text(
                bot,
                chat_id,
                mid,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except TelegramBadRequest:
            try:
                await bot.delete_message(chat_id, mid)
            except TelegramBadRequest:
                pass
    sent = await message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
    set_panel_id(chat_id, user_id, sent.message_id)


async def push_text_panel(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str | None = None,
    disable_web_preview: bool = False,
    force_new: bool = False,
) -> None:
    mid = get_panel_id(chat_id, user_id)
    if mid and not force_new:
        try:
            await _safe_edit_text(
                bot,
                chat_id,
                mid,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_preview=disable_web_preview,
            )
            return
        except TelegramBadRequest:
            try:
                await bot.delete_message(chat_id, mid)
            except TelegramBadRequest:
                pass
    sent = await bot.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
        disable_web_page_preview=disable_web_preview,
    )
    set_panel_id(chat_id, user_id, sent.message_id)


def _photo_media(photo: str):
    """
    Resolve a photo argument into something send_photo / InputMediaPhoto can take.

    Rules:
      * "https://..." or "http://..." → return the URL as-is (Telegram fetches it).
      * "AgACAg..." / Telegram file_id (no slashes, looks like base64-ish) → as-is.
      * Anything else → treat as a local filesystem path → FSInputFile.
    """
    if isinstance(photo, str) and photo.startswith(("http://", "https://")):
        return photo
    # Telegram file_ids are 30-100+ chars, base64url-ish, no path separators.
    if (
        isinstance(photo, str)
        and "/" not in photo
        and "\\" not in photo
        and "." not in photo
        and 20 <= len(photo) <= 200
    ):
        return photo
    return FSInputFile(photo)


async def push_photo_panel(
    *,
    bot: Bot,
    chat_id: int,
    user_id: int,
    photo_path: str,
    caption: str,
    reply_markup: InlineKeyboardMarkup,
    parse_mode: str | None = "Markdown",
) -> None:
    media = _photo_media(photo_path)
    mid = get_panel_id(chat_id, user_id)
    if mid:
        try:
            await _safe_edit_media(
                bot,
                chat_id,
                mid,
                InputMediaPhoto(media=media, caption=caption),
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            return
        except TelegramBadRequest:
            try:
                await bot.delete_message(chat_id, mid)
            except TelegramBadRequest:
                pass
    sent = await bot.send_photo(
        chat_id,
        media,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    set_panel_id(chat_id, user_id, sent.message_id)


async def send_photo_url_cached(
    *,
    bot: Bot,
    chat_id: int,
    photo: str,
    caption: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
    cache: dict[str, str] | None = None,
) -> str | None:
    """
    Send a photo by URL, then re-use the returned `file_id` for subsequent sends
    of the same URL — fewer Telegram fetches, faster delivery.

    Pass a dict (in-memory or persisted) as `cache`. Returns the file_id used so
    callers can store it elsewhere if needed.
    """
    cached_id = cache.get(photo) if cache is not None else None
    sent = await bot.send_photo(
        chat_id,
        cached_id or photo,
        caption=caption,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    if sent.photo and cache is not None:
        file_id = sent.photo[-1].file_id
        cache[photo] = file_id
        return file_id
    return cached_id
