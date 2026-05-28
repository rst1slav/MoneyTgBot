import random
from decimal import Decimal
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import t


def yona_main_menu_keyboard(
    *, wallets_count: int = 0, lang: str = "ru"
) -> InlineKeyboardMarkup:
    wallets_label = f"{t('menu.yona.wallets', lang)} • {wallets_count}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=wallets_label, callback_data="menu:wallets")],
            [InlineKeyboardButton(text=t("menu.yona.p2p", lang), callback_data="menu:p2p")],
            [
                InlineKeyboardButton(text=t("menu.yona.checks", lang), callback_data="menu:checks"),
                InlineKeyboardButton(text=t("menu.yona.invoices", lang), callback_data="menu:invoices"),
            ],
            [InlineKeyboardButton(text=t("menu.yona.subs", lang), callback_data="menu:subs")],
            [
                InlineKeyboardButton(text=t("menu.yona.refs", lang), callback_data="menu:refs"),
                InlineKeyboardButton(text=t("menu.settings", lang), callback_data="menu:settings"),
            ],
        ]
    )


def main_menu(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("menu.add", lang), callback_data="menu:add"),
                InlineKeyboardButton(text=t("menu.profile", lang), callback_data="menu:profile"),
            ],
            [
                InlineKeyboardButton(text=t("menu.history", lang), callback_data="menu:history"),
                InlineKeyboardButton(text=t("menu.integrations", lang), callback_data="menu:integrations"),
            ],
            [
                InlineKeyboardButton(text=t("menu.refresh_gifts", lang), callback_data="profile:refresh"),
            ],
            [
                InlineKeyboardButton(text=t("menu.settings", lang), callback_data="menu:settings"),
            ],
        ]
    )


def profile_period_keyboard(
    *,
    selected_period: str = "week",
    lang: str = "ru",
    selected_view: str = "analytics",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("period.week", lang),
                    callback_data="profile:week",
                    style="success" if selected_period == "week" else None,
                ),
                InlineKeyboardButton(
                    text=t("period.month", lang),
                    callback_data="profile:month",
                    style="success" if selected_period == "month" else None,
                ),
                InlineKeyboardButton(
                    text=t("period.year", lang),
                    callback_data="profile:year",
                    style="success" if selected_period == "year" else None,
                ),
            ],
            [
                InlineKeyboardButton(text=t("menu.history", lang), callback_data="menu:history"),
                InlineKeyboardButton(text=t("menu.integrations", lang), callback_data="menu:integrations"),
            ],
            [
                InlineKeyboardButton(text=t("menu.refresh_gifts", lang), callback_data="profile:refresh"),
            ],
            [
                InlineKeyboardButton(text=t("menu.settings", lang), callback_data="menu:settings"),
            ],
            [
                InlineKeyboardButton(text=t("back", lang), callback_data="menu:home"),
            ],
        ]
    )


def profile_crypto_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("profile.tab.analytics", lang),
                    callback_data="profile:view:analytics",
                    style=None,
                ),
                InlineKeyboardButton(
                    text=t("profile.tab.crypto", lang),
                    callback_data="profile:view:crypto",
                    style="primary",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("profile.crypto.import_seed", lang),
                    callback_data="profile:crypto_import_seed",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("profile.crypto.create_wallet", lang),
                    callback_data="profile:crypto_create_wallet",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t("profile.crypto.refresh", lang),
                    callback_data="profile:crypto_refresh",
                ),
            ],
        ]
    )


# ---------------------------------------------------------------------------
# Settings keyboards
# ---------------------------------------------------------------------------

def settings_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("settings.timezone", lang), callback_data="set:tz")],
            [InlineKeyboardButton(text=t("settings.language", lang), callback_data="set:lang")],
            [InlineKeyboardButton(text=t("settings.currency", lang), callback_data="set:ccy")],
            [InlineKeyboardButton(text=t("settings.support", lang), url="https://t.me/rst1k")],
            [InlineKeyboardButton(text=t("back", lang), callback_data="menu:home")],
        ]
    )


_COMMON_TIMEZONES: list[tuple[str, str]] = [
    ("tz.kyiv", "Europe/Kyiv"),
    ("tz.moscow", "Europe/Moscow"),
    ("tz.minsk", "Europe/Minsk"),
    ("tz.warsaw", "Europe/Warsaw"),
    ("tz.tashkent", "Asia/Tashkent"),
    ("tz.london", "Europe/London"),
    ("tz.berlin", "Europe/Berlin"),
    ("tz.newyork", "America/New_York"),
    (None, "UTC"),  # raw text — no translation needed
]


def settings_timezone_keyboard(
    current: str | None = None, lang: str = "ru"
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pairs = [_COMMON_TIMEZONES[i:i + 2] for i in range(0, len(_COMMON_TIMEZONES), 2)]
    for pair in pairs:
        row: list[InlineKeyboardButton] = []
        for key, tz in pair:
            mark = "✓ " if current == tz else ""
            label = t(key, lang) if key else "UTC"
            row.append(InlineKeyboardButton(
                text=f"{mark}{label}",
                callback_data=f"set:tz_pick:{tz}",
            ))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("settings.tz_custom_btn", lang), callback_data="set:tz_custom")])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="set:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_LANGUAGES: list[tuple[str, str]] = [
    ("🇬🇧 English", "en"),
    ("🇷🇺 Русский", "ru"),
    ("🇺🇦 Українська", "uk"),
]


def settings_language_keyboard(
    current: str | None = None, lang: str = "ru"
) -> InlineKeyboardMarkup:
    row: list[InlineKeyboardButton] = []
    for label, code in _LANGUAGES:
        mark = "✓ " if current == code else ""
        row.append(InlineKeyboardButton(text=f"{mark}{label}", callback_data=f"set:lang_pick:{code}"))
    return InlineKeyboardMarkup(
        inline_keyboard=[
            row,
            [InlineKeyboardButton(text=t("back", lang), callback_data="set:open")],
        ]
    )


_SETTINGS_CURRENCIES = ["UAH", "USD", "EUR", "RUB", "BYN", "PLN", "UZS"]


def settings_currency_keyboard(
    current: str | None = None, lang: str = "ru"
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    chunk = 3
    for i in range(0, len(_SETTINGS_CURRENCIES), chunk):
        row: list[InlineKeyboardButton] = []
        for ccy in _SETTINGS_CURRENCIES[i:i + chunk]:
            mark = "✓ " if current == ccy else ""
            row.append(InlineKeyboardButton(text=f"{mark}{ccy}", callback_data=f"set:ccy_pick:{ccy}"))
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="set:open")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def history_keyboard(
    filters: "HistoryFilters",
    page: int = 1,
    *,
    back_to: str = "menu:home",
    lock_source: bool = False,
    has_prev: bool = True,
    has_next: bool = True,
    total_pages: int | None = None,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    from app.db.models import AccountType, TransactionType

    if total_pages is None or total_pages < 1:
        # Derive a sensible fallback from has_prev/has_next when caller can't count.
        total_pages = max(page, page + (1 if has_next else 0))

    pagination_row: list[InlineKeyboardButton] = [
        InlineKeyboardButton(
            text=label,
            callback_data=f"history:page_{p}" if p != page else "history:noop",
        )
        for label, p in _coin_pagination_buttons(page, total_pages)
    ]
    
    all_type = t("history.filter.all", lang)
    expenses = t("history.filter.expense", lang)
    income = t("history.filter.income", lang)
    all_source = t("history.filter.all", lang)
    manual = "Manual"  # not exposed via UI, kept as constant for legacy callbacks
    card = t("history.filter.card", lang)
    crypto = t("history.filter.crypto", lang)
    all_amt = t("history.filter.all", lang)
    big = t("history.filter.big", lang)
    small = t("history.filter.small", lang)

    rows: list[list[InlineKeyboardButton]] = [
        pagination_row,
        [
            InlineKeyboardButton(
                text=all_type,
                callback_data="history:type_all",
                style="success" if filters.tx_type is None else None,
            ),
            InlineKeyboardButton(
                text=expenses,
                callback_data="history:type_expense",
                style="success" if filters.tx_type == TransactionType.EXPENSE else None,
            ),
            InlineKeyboardButton(
                text=income,
                callback_data="history:type_income",
                style="success" if filters.tx_type == TransactionType.INCOME else None,
            ),
        ],
    ]

    # Source row hidden when the view is locked to a specific account/bank.
    if not lock_source:
        rows.append([
            InlineKeyboardButton(
                text=all_source,
                callback_data="history:source_all",
                style="success" if filters.account_type is None else None,
            ),
            InlineKeyboardButton(
                text=card,
                callback_data="history:source_card",
                style="success" if filters.account_type == AccountType.MONOBANK_CARD else None,
            ),
            InlineKeyboardButton(
                text=crypto,
                callback_data="history:source_crypto",
                style="success" if filters.account_type == AccountType.TON_WALLET else None,
            ),
        ])

    rows.append([
        InlineKeyboardButton(
            text=all_amt,
            callback_data="history:amt_all",
            style="success" if getattr(filters, "size", None) is None else None,
        ),
        InlineKeyboardButton(
            text=big,
            callback_data="history:amt_big",
            style="success" if getattr(filters, "size", None) == "big" else None,
        ),
        InlineKeyboardButton(
            text=small,
            callback_data="history:amt_small",
            style="success" if getattr(filters, "size", None) == "small" else None,
        ),
    ])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data=back_to)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def integrations_categories_keyboard(counts: dict[str, int], lang: str = "ru") -> InlineKeyboardMarkup:
    """Top-level integrations menu: Monobank + Crypto with slot counts."""
    mono = counts.get("monobank", 0)
    ton = counts.get("ton", 0)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=f"{t('profile.monobank', lang)} ({mono}/5)",
                callback_data="int:bank:monobank",
            )],
            [InlineKeyboardButton(
                text=f"{t('profile.crypto', lang)} ({ton}/5)",
                callback_data="int:bank:ton",
            )],
            [InlineKeyboardButton(text=t("back", lang), callback_data="menu:home")],
        ]
    )


def integrations_bank_keyboard(
    *,
    bank_slug: str,
    slots: list[tuple[int | None, str]],
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """
    slots: list of 5 entries. Each is either:
      (account_id, "shortened_ref or custom name") for connected slots,
      (None, "") for empty slots.
    """
    rows = [
        [InlineKeyboardButton(text=t("int.history", lang), callback_data=f"int:hist_bank:{bank_slug}")],
    ]
    for idx, (acc_id, label) in enumerate(slots):
        if acc_id is not None:
            rows.append([
                InlineKeyboardButton(
                    text=label or t("int.connected", lang),
                    callback_data=f"int:slot:{acc_id}",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text=t("int.connect_slot", lang, n=idx + 1),
                    callback_data=f"int:link:{bank_slug}",
                )
            ])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="int:cat_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def integrations_account_keyboard(
    account_id: int,
    bank_slug: str,
    *,
    has_custom_name: bool = False,  # kept for signature compatibility
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("int.action.rename", lang), callback_data=f"int:rename:{account_id}")],
            [InlineKeyboardButton(text=t("int.action.unlink", lang), callback_data=f"int:unlink_ask:{account_id}")],
            [InlineKeyboardButton(text=t("int.history", lang), callback_data=f"int:hist_acc:{account_id}")],
            [InlineKeyboardButton(text=t("back", lang), callback_data=f"int:bank:{bank_slug}")],
        ]
    )


def integrations_rename_keyboard(
    account_id: int,
    *,
    has_custom_name: bool = False,
    lang: str = "ru",
) -> InlineKeyboardMarkup:
    """Keyboard shown while waiting for the new custom name."""
    rows: list[list[InlineKeyboardButton]] = []
    if has_custom_name:
        rows.append([
            InlineKeyboardButton(
                text=t("int.action.reset_name", lang),
                callback_data=f"int:rename_clear:{account_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(text=t("back", lang), callback_data=f"int:slot:{account_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def integrations_unlink_confirm_keyboard(account_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=t("int.unlink_yes", lang), callback_data=f"int:unlink_yes:{account_id}"),
                InlineKeyboardButton(text=t("int.unlink_no", lang), callback_data=f"int:slot:{account_id}"),
            ],
        ]
    )


def integrations_pending_keyboard(back_to: str = "int:cat_menu", lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("back", lang), callback_data=back_to)],
        ]
    )


def back_home_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("back", lang), callback_data="menu:home")]]
    )


def _coin_pagination_buttons(
    current_page: int, total_pages: int, max_btns: int = 5
) -> list[tuple[str, int]]:
    """Returns [(label, page_num), ...] sized to fit `max_btns` slots."""
    # Маркер выбранной страницы: «• 1 •» (с пробелами вокруг номера).
    def _sel(p: int) -> str:
        return f"• {p} •"

    if total_pages <= 1:
        return [(_sel(1), 1)]
    if total_pages <= max_btns:
        return [
            (_sel(p) if p == current_page else str(p), p)
            for p in range(1, total_pages + 1)
        ]
    # total_pages > max_btns: window + last-page jump.
    if current_page <= max_btns - 2:
        # Show first (max_btns - 1) pages, with "›" on the boundary, then last "›››".
        out: list[tuple[str, int]] = []
        for p in range(1, max_btns - 1):
            out.append((_sel(p) if p == current_page else str(p), p))
        boundary = max_btns - 1
        out.append((_sel(boundary) if boundary == current_page else f"{boundary} ›", boundary))
        out.append((f"{total_pages} ›››", total_pages))
        return out
    if current_page >= total_pages - (max_btns - 3):
        # Near the end: ‹‹‹ 1, then last (max_btns - 1) pages.
        out = [(f"‹‹‹ 1", 1)]
        start = total_pages - (max_btns - 2)
        for p in range(start, total_pages + 1):
            out.append((_sel(p) if p == current_page else str(p), p))
        return out
    # Middle: ‹‹‹ 1, prev, • current •, next ›, last ›››
    return [
        (f"‹‹‹ 1", 1),
        (str(current_page - 1), current_page - 1),
        (_sel(current_page), current_page),
        (f"{current_page + 1} ›", current_page + 1),
        (f"{total_pages} ›››", total_pages),
    ]


def crypto_main_keyboard(
    wallets: list[tuple[int, str]],
    current_idx: int = 0,
    lang: str = "ru",
    *,
    coin_page: int = 1,
    coin_total_pages: int = 1,
) -> InlineKeyboardMarkup:
    if wallets and 0 <= current_idx < len(wallets):
        label = wallets[current_idx][1]
        has_prev = current_idx > 0
        has_next = current_idx < len(wallets) - 1
    else:
        label = "—"
        has_prev = has_next = False

    # Wallet switcher: empty arrow slots collapse into the "🆕 add wallet" button.
    if has_prev:
        left_btn = InlineKeyboardButton(text="⬅️", callback_data="crypto:prev")
    else:
        left_btn = InlineKeyboardButton(text="🆕", callback_data="crypto:new_menu")
    if has_next:
        right_btn = InlineKeyboardButton(text="➡️", callback_data="crypto:next")
    else:
        right_btn = InlineKeyboardButton(text="🆕", callback_data="crypto:new_menu")
    switcher_row: list[InlineKeyboardButton] = [
        left_btn,
        InlineKeyboardButton(text=label, callback_data="crypto:noop"),
        right_btn,
    ]

    page_row = [
        InlineKeyboardButton(
            text=label_, callback_data=f"crypto:page:{page_}" if page_ != coin_page else "crypto:noop"
        )
        for label_, page_ in _coin_pagination_buttons(coin_page, coin_total_pages)
    ]

    rows: list[list[InlineKeyboardButton]] = [
        switcher_row,
        page_row,
        [
            InlineKeyboardButton(
                text=t("crypto.deposit", lang),
                callback_data="crypto:deposit" if wallets else "crypto:noop",
            ),
            InlineKeyboardButton(
                text=t("crypto.withdraw", lang),
                callback_data="crypto:withdraw" if wallets else "crypto:noop",
            ),
        ],
        [
            InlineKeyboardButton(text=t("menu.history", lang), callback_data="crypto:history"),
        ],
        [
            InlineKeyboardButton(text=t("crypto.configure", lang), callback_data="crypto:settings"),
        ],
        [
            InlineKeyboardButton(text=t("back", lang), callback_data="menu:home"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_deposit_keyboard(
    lang: str = "ru", *, qr_shown: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура экрана пополнения. QR-кнопка переключает показ/скрытие."""
    qr_text = (
        t("crypto.deposit.hide_qr", lang) if qr_shown
        else t("crypto.deposit.show_qr", lang)
    )
    qr_cb = "crypto:dep_hide_qr" if qr_shown else "crypto:dep_show_qr"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=qr_text, callback_data=qr_cb)],
            [InlineKeyboardButton(
                text=t("crypto.deposit.no_payment", lang),
                callback_data="crypto:dep_help",
            )],
            [InlineKeyboardButton(text=t("back", lang), callback_data="crypto:refresh")],
        ]
    )


def crypto_deposit_help_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Экран 'не пришёл депозит?': кнопки проверки и поддержки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=t("crypto.deposit.check_btn", lang),
                callback_data="crypto:dep_check",
            )],
            [InlineKeyboardButton(
                text=t("crypto.deposit.support_btn", lang),
                callback_data="crypto:dep_support",
            )],
            [InlineKeyboardButton(text=t("back", lang), callback_data="crypto:deposit")],
        ]
    )


def crypto_settings_keyboard(
    lang: str = "ru", *, has_wallet: bool = True
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_wallet:
        rows.append([InlineKeyboardButton(text=t("crypto.display_btn", lang), callback_data="crypto:display")])
        rows.append([InlineKeyboardButton(text=t("crypto.rename_btn", lang), callback_data="crypto:rename")])
        rows.append([InlineKeyboardButton(text=t("crypto.info_btn", lang), callback_data="crypto:info")])
        rows.append([InlineKeyboardButton(text=t("crypto.unlink_btn", lang), callback_data="crypto:unlink_ask")])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="crypto:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_new_wallet_menu_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("crypto.new.create_btn", lang), callback_data="crypto:new_create")],
            [InlineKeyboardButton(text=t("crypto.new.import_btn", lang), callback_data="crypto:new_import")],
            [InlineKeyboardButton(text=t("back", lang), callback_data="crypto:refresh")],
        ]
    )


def crypto_import_prompt_keyboard(lang: str = "ru", *, back_to: str = "crypto:new_menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("back", lang), callback_data=back_to)]]
    )


def crypto_create_success_keyboard(lang: str = "ru", *, seed: str = "") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if seed:
        rows.append([
            InlineKeyboardButton(
                text=t("crypto.create.copy_btn", lang),
                copy_text=CopyTextButton(text=seed),
            )
        ])
    rows.append([
        InlineKeyboardButton(text=t("crypto.create.to_wallet_btn", lang), callback_data="crypto:refresh")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_info_with_copy_keyboard(lang: str = "ru", *, seed: str = "") -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if seed:
        rows.append([
            InlineKeyboardButton(
                text=t("crypto.create.copy_btn", lang),
                copy_text=CopyTextButton(text=seed),
            )
        ])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="crypto:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_empty_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("crypto.new.create_btn", lang), callback_data="crypto:new_create")],
            [InlineKeyboardButton(text=t("crypto.new.import_btn", lang), callback_data="crypto:new_import")],
            [InlineKeyboardButton(text=t("back", lang), callback_data="menu:home")],
        ]
    )


def crypto_info_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t("back", lang), callback_data="crypto:settings")]]
    )


def crypto_display_mode_keyboard(
    current_mode: str, lang: str = "ru"
) -> InlineKeyboardMarkup:
    modes = [
        ("all", "crypto.display.all"),
        ("min1usd", "crypto.display.min1usd"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=t(label_key, lang),
                callback_data=f"crypto:display_set:{mode}",
                style="success" if current_mode == mode else None,
            )
        ]
        for mode, label_key in modes
    ]
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="crypto:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def crypto_unlink_confirm_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    yes = InlineKeyboardButton(text=t("int.unlink_yes", lang), callback_data="crypto:unlink_yes")
    no = InlineKeyboardButton(text=t("int.unlink_no", lang), callback_data="crypto:settings")
    row = [yes, no]
    random.shuffle(row)
    return InlineKeyboardMarkup(inline_keyboard=[row])


def crypto_rename_keyboard(
    lang: str = "ru", *, has_custom: bool = False
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_custom:
        rows.append([
            InlineKeyboardButton(
                text=t("crypto.rename_clear_btn", lang),
                callback_data="crypto:rename_clear",
            )
        ])
    rows.append([InlineKeyboardButton(text=t("back", lang), callback_data="crypto:settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
