"""Lightweight translation module.

Usage:
    from app.i18n import t
    text = t("settings.title", "ru")            # → "⚙️ Настройки"
    text = t("profile.income", "uk", n=5)       # supports format()

Lookup falls back to Russian, then to the key itself if missing.
"""
from __future__ import annotations

_TRANSLATIONS: dict[str, dict[str, str]] = {
    # ---------- Languages ----------
    "ru": {
        # Common
        "back": "‹ Назад",
        "cancel": "Отмена",
        "yes": "Да",
        "no": "Нет",
        "saved": "Сохранено",
        "loading": "⏳ Загружаю...",
        "in_development": "⚠️ Интеграция в разработке.",
        "error_load_profile": "Ошибка при загрузке профиля. Попробуйте позже.",
        "error_balance_data": "Ошибка при получении данных баланса.",
        "inline.also": "Также",
        "inline.refresh": "Обновить курс",
        "inline.title_to": "{amount} {base} → {target}",
        "inline.share_profile": "Отправить свой профиль",
        "inline.share_profile_desc": "Текстовый профиль без фото",
        "inline.conversion_desc": "Конвертация с обновлением курса",
        "inline.in_1_days": "за 1 день",
        "inline.in_7_days": "за 7 дней",
        "inline.in_30_days": "за 30 дней",
        "inline.refresh_throttle": "Подожди немного",

        # Main menu
        "menu.add": "➕ Добавить операцию",
        "menu.profile": "📊 Профиль",
        "menu.history": "🧾 История",
        "menu.integrations": "🔗 Интеграции",
        "menu.refresh_gifts": "🔄 Обновить подарки",
        "menu.settings": "⚙️ Настройки",

        # Profile sections
        "profile.total_balance": "🏆 Общий баланс",
        "profile.monobank": "🟢 Monobank",
        "profile.crypto": "💎 Crypto",
        "profile.gifts": "🎁 Подарки",
        "profile.gifts_basic": "Неулучшенные",

        # Period selector
        "period.week": "Неделя",
        "period.month": "Месяц",
        "period.year": "Год",

        # Chart
        "chart.title": "Финансы",
        "chart.income": "Доходы",
        "chart.expense": "Расходы",
        "chart.balance": "Баланс профиля",
        "chart.card_balance": "Карты",
        "chart.crypto_balance": "Крипта",
        "chart.max_income": "Макс. доход",
        "chart.max_expense": "Макс. расход",
        "chart.min_balance": "Мин. баланс",
        "chart.days_label": "{n} дн",
        "chart.expenses_30d": "Расходы за 30 дней",
        "chart.expense_by_category": "Расходы по категориям — 30 дней",
        "chart.income_by_category": "Доходы по категориям — 30 дней",
        "chart.income_vs_expense": "Доходы и расходы — 30 дней",
        "chart.no_data": "Нет данных за выбранный период",
        "chart.other": "Прочее",

        # History
        "history.empty": "По выбранным фильтрам операций не найдено.",
        "history.truncated": "… (обрезано)",
        "history.page": "Стр. {n}",
        "history.filter.all": "Все",
        "history.filter.expense": "Траты",
        "history.filter.income": "Доходы",
        "history.filter.card": "Карта",
        "history.filter.crypto": "Крипта",
        "history.filter.big": "Крупные",
        "history.filter.small": "Мелкие",

        # Integrations
        "int.title": "🔗 Интеграции",
        "int.choose_section": "Выберите раздел:",
        "int.history": "🧾 История",
        "int.connect_slot": "➕ Подключить (слот {n})",
        "int.income": "📥 Доходы",
        "int.expense": "📤 Расходы",
        "int.operations": "🧾 Операций",
        "int.no_expenses_30d": "Нет расходов за последние 30 дней.",
        "int.account_not_found": "Аккаунт не найден.",
        "int.unlink_confirm": "⚠️ Точно отвязать этот аккаунт?",
        "int.unlinked": "Отвязано",
        "int.slot_limit": "Достигнут лимит в {n} подключений.",
        "int.unlink_yes": "✅ Да, отвязать",
        "int.unlink_no": "❌ Отмена",
        "int.account_card": "Карта",
        "int.action.rename": "✏️ Изменить название",
        "int.action.unlink": "🔓 Отвязать",
        "int.action.reset_name": "🗑 Сбросить название",
        "int.rename_prompt": "Отправьте кастомное название для этого аккаунта.",
        "int.mono_link_prompt": (
            "Введите токен и (необязательно) ID/PAN карты через пробел:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "или только <code>&lt;token&gt;</code> для первой карты."
        ),
        "int.ton_link_prompt": "Отправьте адрес TON-кошелька одним сообщением.",
        "int.link_failed_card": "❌ Не удалось привязать карту. Попробуй ещё раз.",
        "int.link_failed_wallet": "❌ Не удалось привязать кошелёк. Попробуй ещё раз.",

        # Settings
        "settings.title": "⚙️ Настройки",
        "settings.timezone": "🕒 Часовой пояс",
        "settings.timezone_label": "Часовой пояс",
        "settings.language": "🌐 Язык бота",
        "settings.language_label": "Язык",
        "settings.currency": "💱 Основная валюта",
        "settings.currency_label": "Основная валюта",
        "settings.currency_hint": "К ней будут конвертироваться суммы в первую очередь.",
        "settings.support": "💬 Написать в поддержку",
        "settings.tz_pick_hint": "Выберите из списка или укажите свой.",
        "settings.tz_custom": "✏️ Свой...",
        "settings.tz_input_prompt": (
            "Отправьте свой часовой пояс одним сообщением.\n\n"
            "Форматы:\n"
            "• IANA-имя: <code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>\n"
            "• UTC-смещение: <code>+02:00</code>, <code>-05:30</code>, <code>+03</code>"
        ),
        "settings.tz_invalid": (
            "❌ Не распознал формат. Примеры:\n"
            "<code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>, "
            "<code>+02:00</code>, <code>-05:30</code>"
        ),
        "settings.timezone_now": "Сейчас: <code>{tz}</code>",
        "settings.tz_not_set": "не указан",
        "settings.tz_custom_btn": "✏️ Свой...",

        # Integrations — additional
        "int.error": "Ошибка",
        "int.empty_token": "Пустой токен.",
        "int.invalid_token_fmt": (
            "Неверный формат токена. Monobank токен — длинная строка из латинских "
            "букв/цифр (≥30 символов). Получить можно у @MonobankClientApiBot."
        ),
        "int.token_not_working": (
            "Токен не работает или Monobank API недоступен. Проверь токен и попробуй позже."
        ),
        "int.card_not_found": (
            "Карта/счёт «{card}» не найдены в этом аккаунте Monobank. "
            "Укажи последние 4 цифры карты, либо отправь только токен (привяжется первый счёт)."
        ),
        "int.empty_address": "Пустой адрес.",
        "int.invalid_ton_addr": (
            "Неверный формат TON-адреса. Ожидается 48-символьный адрес "
            "(EQ.../UQ...) либо raw-форма «0:hex...»."
        ),
        "int.ton_not_found": "Адрес не найден в TON блокчейне.",
        "int.connected": "Подключено",
        "int.mono_link_prompt2": (
            "Введите токен и (необязательно) ID/PAN карты через пробел:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "или только <code>&lt;token&gt;</code> для первой карты.\n\n"
            'Токен можно получить здесь: <a href="https://api.monobank.ua/index.html">api.monobank.ua</a>'
        ),

        # Profile
        "profile.title": "Профиль {name}",
        "profile.unimproved": "Неулучшенные",
        "profile.refresh_toast": "Обновляю подарки...",
        "profile.custom_text_prompt": "Отправьте следующим сообщением новый текст профиля.",
        "profile.tab.analytics": "📊 Аналитический",
        "profile.tab.crypto": "💎 Crypto",
        "profile.crypto.title": "💎 Crypto кошелек",
        "profile.crypto.subtitle": "Импортируйте TON-кошельки по seed-фразе или создавайте новые.",
        "profile.crypto.import_seed": "📥 Импорт по seed-фразе",
        "profile.crypto.create_wallet": "✨ Создать TON-кошелек",
        "profile.crypto.refresh": "🔄 Обновить список",
        "profile.crypto.empty": "Пока нет импортированных кошельков.",
        "profile.crypto.wallets": "Кошельки:",
        "profile.crypto.import_prompt": (
            "Отправьте seed-фразу и адрес кошелька одним сообщением.\n\n"
            "Формат:\n"
            "<code>word1 word2 ... word12 | EQ...</code>"
        ),
        "profile.crypto.import_invalid": "❌ Неверный формат. Нужны seed-фраза (12/24 слова) и TON-адрес.",
        "profile.crypto.import_saved": "✅ Кошелек импортирован.",
        "profile.crypto.create_text": (
            "⚠️ Сохраните seed-фразу в безопасном месте. Мы покажем ее только один раз.\n\n"
            "<code>{seed}</code>\n\n"
            "После импорта в TON-кошелек отправьте адрес в формате:\n"
            "<code>address | EQ...</code>"
        ),
        "profile.crypto.create_waiting_addr": "Ожидаю адрес для нового кошелька.",
        "profile.crypto.create_saved": "✅ Новый кошелек создан и подключен.",
        "profile.crypto.create_invalid_addr": "❌ Неверный TON-адрес.",
        "profile.gifts_emoji_prompt": (
            "Отправь следующим сообщением маппинг подарков.\n"
            "Формат каждой строки (эмодзи в начале):\n"
            "<code>🐰 https://t.me/nft/JellyBunny-57735</code>\n"
            "или просто: <code>🐰 JellyBunny</code>"
        ),
        "profile.gifts_emoji_saved": "✅ Сохранено:",
        "profile.gifts_emoji_invalid": "Ничего не распознал. Проверь формат.",
        "profile.basic_gifts_prompt": (
            "Отправь списком изменения неулучшенных подарков.\n"
            "Добавить/изменить цену:\n"
            "<code>Название, ID, цена_usd</code>\n"
            "Удалить из списка:\n"
            "<code>del, ID</code>"
        ),
        "profile.basic_gifts_saved": "✅ Сохранено. Обновлено/добавлено: {changed}, удалено: {removed}.",
        "profile.basic_gifts_invalid": "⚠️ Не распознаны строки:",

        # Transactions
        "tx.invalid_input": "{err}\n\nПример: -250 грн еда обед",
        "tx.saved": "Операция сохранена: {tx_type} {amount} {currency} [{category}] {desc}",
        "tx.integrations_intro": "Управление интеграциями:",

        # Timezone city labels
        "tz.kyiv": "🇺🇦 Киев",
        "tz.moscow": "🇷🇺 Москва",
        "tz.minsk": "🇧🇾 Минск",
        "tz.warsaw": "🇵🇱 Варшава",
        "tz.tashkent": "🇺🇿 Ташкент",
        "tz.london": "🇬🇧 Лондон",
        "tz.berlin": "🇩🇪 Берлин",
        "tz.newyork": "🇺🇸 Нью-Йорк",

        # Crypto main screen buttons
        "crypto.deposit": "📥 Пополнить",
        "crypto.deposit.title": "📥 Пополнение",
        "crypto.deposit.body": (
            "Для пополнения баланса Вашего кошелька в сети <b>TON</b>, "
            "отправьте монеты на адрес указанный ниже:\n"
            "Адрес: <code>{addr}</code>\n\n"
            "Минимальная сумма: <b>0.0001 TON</b> в любой монете.\n\n"
            "⚠️ Внимание! Пополняйте только монетами в сети <b>TON</b>. "
            "Если Вы используете другую сеть, Ваши монеты <b>будут потеряны</b>."
        ),
        "crypto.deposit.show_qr": "Показать QR-код",
        "crypto.deposit.hide_qr": "Скрыть QR-код",
        "crypto.deposit.no_payment": "Не пришел депозит?",
        "crypto.deposit.help_title": "Не пришел депозит?",
        "crypto.deposit.help_body": (
            "Возможно, перевод был выполнен со смарт-контракта вместо перевода "
            "с кошелька, поэтому убедитесь, что средства успешно поступили на "
            "ваш кошелек в блокчейне.\n\n"
            "После этого нажмите кнопку «Проверить депозит». В случае если "
            "депозит так и не дошел — свяжитесь с поддержкой."
        ),
        "crypto.deposit.check_btn": "🔍 Проверить депозит",
        "crypto.deposit.support_btn": "💬 Связаться с поддержкой",
        "crypto.deposit.check_in_progress": "Проверяю…",
        "crypto.deposit.check_done": "Баланс обновлён",
        "crypto.withdraw": "📤 Вывести",
        "crypto.configure": "⚙️ Настроить",
        "crypto.stats": "📊 Статистика",
        "crypto.my_wallet": "Кошелёк",
        "crypto.deposit_addr_label": "📥 Адрес для пополнения",
        "crypto.display_btn": "💼 Отображение балансов",
        "crypto.rename_btn": "🖍 Переименовать",
        "crypto.unlink_btn": "⛓️‍💥 Отвязать кошелек",
        "crypto.display_title": "💼 Отображение балансов",
        "crypto.display_subtitle": "Выберите как отображать баланс Вашего кошелька:",
        "crypto.display.all": "Показывать все",
        "crypto.display.nonzero": "Скрывать нулевые",
        "crypto.display.min1usd": "Скрывать менее 1 USD",
        "crypto.rename_prompt": (
            "<b>🖍 Название кошелька</b>\n\n"
            "Отправьте новое название вашего кошелька для удобного отображения в боте. "
            "По умолчанию: Кошелёк"
        ),
        "crypto.rename_clear_btn": "🗑 Удалить название",
        "crypto.rename_saved": "✅ Название обновлено.",
        "crypto.settings_title": "⚙️ Настройки",
        "crypto.display_status": "💼 Отображение балансов: {mode}",
        "crypto.display.all_short": "Все",
        "crypto.display.min1usd_short": "Менее 1 USD скрыты",
        "crypto.new.title": "👛 Новый кошелёк",
        "crypto.new.subtitle": "Создайте или импортируйте до 10 кошельков.",
        "crypto.new.create_btn": "💎 Создать новый (TON)",
        "crypto.new.import_btn": "🔗 Импортировать",
        "crypto.import.title": "🔗 Импортировать кошелёк",
        "crypto.import.subtitle": "Отправьте seed-фразу одним сообщением",
        "crypto.import.not_found": "❌ Кошелёк не был найден",
        "crypto.create.success_title": "✅ Кошелёк успешно создан!",
        "crypto.create.success_subtitle": "Сохраните seed-фразу в надежном месте, чтобы не потерять доступ к кошельку:",
        "crypto.create.copy_btn": "📋 Скопировать",
        "crypto.create.to_wallet_btn": "👛 К кошельку",
        "crypto.empty.title": "ℹ️ У вас еще нет кошелька",
        "crypto.empty.subtitle": "Создайте его прямо сейчас либо импортируйте уже существующий",
        "crypto.info_btn": "ℹ️ Информация",
        "crypto.info.title": "ℹ️ Информация",
        "crypto.info.seed_label": "seed-фраза от вашего кошелька:",
        "crypto.info.warning": "⚠️ Никому не пересылайте это сообщение! Наши сотрудники никогда не попросят данные от ваших кошельков.",

        # Main (Yona) menu
        "menu.yona.text": (
            '🗃 <a href="https://t.me/YonaWBot">Yona</a> - больше чем просто криптокошелёк. '
            "Отправляйте, получайте и храните криптовалюту не выходя из Telegram.\n\n"
            'Узнавайте обо всех новостях в <a href="https://t.me/YonaWallet">официальном канале</a>'
        ),
        "menu.yona.wallets": "👛 Кошельки",
        "menu.yona.p2p": "🅿️ P2P",
        "menu.yona.checks": "🔖 Чеки",
        "menu.yona.invoices": "📝 Счета",
        "menu.yona.subs": "📅 Подписки",
        "menu.yona.refs": "👨‍🌾 Рефералы",
        "crypto.unlink_confirm": (
            "<b>⚠️ Точно отвязать этот кошелек?</b>\n\n"
            "Если вы не сохранили seed-фразу то больше не сможете вернуть этот кошелёк."
        ),
        "crypto.unlinked": "Кошелек отвязан",
        "crypto.no_wallet": "Нет подключённого кошелька.",
        "crypto.stats.no_data": "Пока недостаточно данных для статистики.",
        "crypto.stats.delta_up": "выросло на {pct:.2f}%",
        "crypto.stats.delta_down": "упало на {pct:.2f}%",
        "crypto.stats.delta_flat": "без изменений",
        "crypto.stats.period_label": "За {period}: {delta}",
        "crypto.stats.title": "📊 Статистика — {wallet}",
    },
    "en": {
        "back": "‹ Back",
        "cancel": "Cancel",
        "yes": "Yes",
        "no": "No",
        "saved": "Saved",
        "loading": "⏳ Loading...",
        "in_development": "⚠️ Integration is in development.",
        "error_load_profile": "Failed to load profile. Please try again later.",
        "error_balance_data": "Failed to fetch balance data.",
        "inline.also": "Also",
        "inline.refresh": "Refresh rate",
        "inline.title_to": "{amount} {base} → {target}",
        "inline.share_profile": "Share my profile",
        "inline.share_profile_desc": "Text profile without picture",
        "inline.conversion_desc": "Conversion with refresh button",
        "inline.in_1_days": "in 1 day",
        "inline.in_7_days": "in 7 days",
        "inline.in_30_days": "in 30 days",
        "inline.refresh_throttle": "Hold on a moment",

        "menu.add": "➕ Add operation",
        "menu.profile": "📊 Profile",
        "menu.history": "🧾 History",
        "menu.integrations": "🔗 Integrations",
        "menu.refresh_gifts": "🔄 Refresh gifts",
        "menu.settings": "⚙️ Settings",

        "profile.total_balance": "🏆 Total balance",
        "profile.monobank": "🟢 Monobank",
        "profile.crypto": "💎 Crypto",
        "profile.gifts": "🎁 Gifts",
        "profile.gifts_basic": "Common",

        "period.week": "Week",
        "period.month": "Month",
        "period.year": "Year",

        "chart.title": "Finances",
        "chart.income": "Income",
        "chart.expense": "Expense",
        "chart.balance": "Profile balance",
        "chart.card_balance": "Cards",
        "chart.crypto_balance": "Crypto",
        "chart.max_income": "Max income",
        "chart.max_expense": "Max expense",
        "chart.min_balance": "Min balance",
        "chart.days_label": "{n} days",
        "chart.expenses_30d": "Expenses (30 days)",
        "chart.expense_by_category": "Expenses by category — 30 days",
        "chart.income_by_category": "Income by category — 30 days",
        "chart.income_vs_expense": "Income vs Expense — 30 days",
        "chart.no_data": "No data for the selected period",
        "chart.other": "Other",

        # History
        "history.empty": "No transactions match the filters.",
        "history.truncated": "… (truncated)",
        "history.page": "Page {n}",
        "history.filter.all": "All",
        "history.filter.expense": "Expense",
        "history.filter.income": "Income",
        "history.filter.card": "Card",
        "history.filter.crypto": "Crypto",
        "history.filter.big": "Big",
        "history.filter.small": "Small",

        # Integrations
        "int.title": "🔗 Integrations",
        "int.choose_section": "Choose a section:",
        "int.history": "🧾 History",
        "int.connect_slot": "➕ Connect (slot {n})",
        "int.income": "📥 Income",
        "int.expense": "📤 Expense",
        "int.operations": "🧾 Operations",
        "int.no_expenses_30d": "No expenses in the last 30 days.",
        "int.account_not_found": "Account not found.",
        "int.unlink_confirm": "⚠️ Unlink this account for sure?",
        "int.unlinked": "Unlinked",
        "int.slot_limit": "Slot limit reached: {n}.",
        "int.unlink_yes": "✅ Yes, unlink",
        "int.unlink_no": "❌ Cancel",
        "int.account_card": "Card",
        "int.action.rename": "✏️ Rename",
        "int.action.unlink": "🔓 Unlink",
        "int.action.reset_name": "🗑 Reset name",
        "int.rename_prompt": "Send a custom name for this account.",
        "int.mono_link_prompt": (
            "Send the token and (optionally) card ID/PAN, space-separated:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "or just <code>&lt;token&gt;</code> for the first card."
        ),
        "int.ton_link_prompt": "Send a TON wallet address in one message.",
        "int.link_failed_card": "❌ Failed to link the card. Try again.",
        "int.link_failed_wallet": "❌ Failed to link the wallet. Try again.",

        "settings.title": "⚙️ Settings",
        "settings.timezone": "🕒 Timezone",
        "settings.timezone_label": "Timezone",
        "settings.language": "🌐 Bot language",
        "settings.language_label": "Language",
        "settings.currency": "💱 Primary currency",
        "settings.currency_label": "Primary currency",
        "settings.currency_hint": "Amounts are converted to it by default.",
        "settings.support": "💬 Contact support",
        "settings.tz_pick_hint": "Pick one or enter your own.",
        "settings.tz_custom": "✏️ Custom...",
        "settings.tz_input_prompt": (
            "Send your timezone in one message.\n\n"
            "Formats:\n"
            "• IANA name: <code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>\n"
            "• UTC offset: <code>+02:00</code>, <code>-05:30</code>, <code>+03</code>"
        ),
        "settings.tz_invalid": (
            "❌ Format not recognized. Examples:\n"
            "<code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>, "
            "<code>+02:00</code>, <code>-05:30</code>"
        ),
        "settings.timezone_now": "Now: <code>{tz}</code>",
        "settings.tz_not_set": "not set",
        "settings.tz_custom_btn": "✏️ Custom...",

        "int.error": "Error",
        "int.empty_token": "Empty token.",
        "int.invalid_token_fmt": (
            "Invalid token format. A Monobank token is a long alphanumeric string "
            "(≥30 chars). Get one from @MonobankClientApiBot."
        ),
        "int.token_not_working": (
            "Token not working or Monobank API unavailable. Check the token and try again."
        ),
        "int.card_not_found": (
            "Card/account «{card}» not found in this Monobank account. "
            "Try the last 4 digits, or send only the token to bind the first account."
        ),
        "int.empty_address": "Empty address.",
        "int.invalid_ton_addr": (
            "Invalid TON address format. Expected 48-char user-friendly address "
            "(EQ.../UQ...) or raw form «0:hex...»."
        ),
        "int.ton_not_found": "Address not found on TON blockchain.",
        "int.connected": "Connected",
        "int.mono_link_prompt2": (
            "Send the token and (optionally) card ID/PAN, space-separated:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "or just <code>&lt;token&gt;</code> for the first card.\n\n"
            'Get a token here: <a href="https://api.monobank.ua/index.html">api.monobank.ua</a>'
        ),

        "profile.title": "Profile {name}",
        "profile.unimproved": "Common",
        "profile.refresh_toast": "Refreshing gifts...",
        "profile.custom_text_prompt": "Send the new profile text in the next message.",
        "profile.tab.analytics": "📊 Analytics",
        "profile.tab.crypto": "💎 Crypto",
        "profile.crypto.title": "💎 Crypto wallet",
        "profile.crypto.subtitle": "Import TON wallets via seed phrase or create new ones.",
        "profile.crypto.import_seed": "📥 Import by seed phrase",
        "profile.crypto.create_wallet": "✨ Create TON wallet",
        "profile.crypto.refresh": "🔄 Refresh list",
        "profile.crypto.empty": "No imported wallets yet.",
        "profile.crypto.wallets": "Wallets:",
        "profile.crypto.import_prompt": (
            "Send seed phrase and wallet address in one message.\n\n"
            "Format:\n"
            "<code>word1 word2 ... word12 | EQ...</code>"
        ),
        "profile.crypto.import_invalid": "❌ Invalid format. Need seed phrase (12/24 words) and TON address.",
        "profile.crypto.import_saved": "✅ Wallet imported.",
        "profile.crypto.create_text": (
            "⚠️ Save this seed phrase in a safe place. It is shown only once.\n\n"
            "<code>{seed}</code>\n\n"
            "After importing it into your TON wallet app, send address as:\n"
            "<code>address | EQ...</code>"
        ),
        "profile.crypto.create_waiting_addr": "Waiting for address for the new wallet.",
        "profile.crypto.create_saved": "✅ New wallet created and linked.",
        "profile.crypto.create_invalid_addr": "❌ Invalid TON address.",
        "profile.gifts_emoji_prompt": (
            "Send gift emoji mapping in the next message.\n"
            "Each line (emoji first):\n"
            "<code>🐰 https://t.me/nft/JellyBunny-57735</code>\n"
            "or just: <code>🐰 JellyBunny</code>"
        ),
        "profile.gifts_emoji_saved": "✅ Saved:",
        "profile.gifts_emoji_invalid": "Nothing recognized. Check the format.",
        "profile.basic_gifts_prompt": (
            "Send a list of common-gift changes.\n"
            "Add/update price:\n"
            "<code>Name, ID, price_usd</code>\n"
            "Remove from list:\n"
            "<code>del, ID</code>"
        ),
        "profile.basic_gifts_saved": "✅ Saved. Updated/added: {changed}, removed: {removed}.",
        "profile.basic_gifts_invalid": "⚠️ Unrecognized lines:",

        "tx.invalid_input": "{err}\n\nExample: -250 uah food lunch",
        "tx.saved": "Saved: {tx_type} {amount} {currency} [{category}] {desc}",
        "tx.integrations_intro": "Manage integrations:",

        "tz.kyiv": "🇺🇦 Kyiv",
        "tz.moscow": "🇷🇺 Moscow",
        "tz.minsk": "🇧🇾 Minsk",
        "tz.warsaw": "🇵🇱 Warsaw",
        "tz.tashkent": "🇺🇿 Tashkent",
        "tz.london": "🇬🇧 London",
        "tz.berlin": "🇩🇪 Berlin",
        "tz.newyork": "🇺🇸 New York",

        # Crypto main screen buttons
        "crypto.deposit": "📥 Deposit",
        "crypto.deposit.title": "📥 Deposit",
        "crypto.deposit.body": (
            "To top up your wallet on the <b>TON</b> network, send coins to the "
            "address below:\n"
            "Address: <code>{addr}</code>\n\n"
            "Minimum amount: <b>0.0001 TON</b> in any coin.\n\n"
            "⚠️ Warning! Only send coins on the <b>TON</b> network. "
            "If you use a different network, your coins <b>will be lost</b>."
        ),
        "crypto.deposit.show_qr": "Show QR code",
        "crypto.deposit.hide_qr": "Hide QR code",
        "crypto.deposit.no_payment": "Deposit didn't arrive?",
        "crypto.deposit.help_title": "Deposit didn't arrive?",
        "crypto.deposit.help_body": (
            "The transfer may have been sent from a smart contract instead of a "
            "wallet, so make sure the funds successfully arrived to your wallet "
            "on the blockchain.\n\n"
            "Then press «Check deposit». If the deposit still didn't arrive — "
            "contact support."
        ),
        "crypto.deposit.check_btn": "🔍 Check deposit",
        "crypto.deposit.support_btn": "💬 Contact support",
        "crypto.deposit.check_in_progress": "Checking…",
        "crypto.deposit.check_done": "Balance updated",
        "crypto.withdraw": "📤 Withdraw",
        "crypto.configure": "⚙️ Configure",
        "crypto.stats": "📊 Statistics",
        "crypto.my_wallet": "Wallet",
        "crypto.deposit_addr_label": "📥 Deposit address",
        "crypto.display_btn": "💼 Balance display",
        "crypto.rename_btn": "🖍 Rename",
        "crypto.unlink_btn": "⛓️‍💥 Unlink wallet",
        "crypto.display_title": "💼 Balance display",
        "crypto.display_subtitle": "Choose how to display your wallet balance:",
        "crypto.display.all": "Show all",
        "crypto.display.nonzero": "Hide zero",
        "crypto.display.min1usd": "Hide under 1 USD",
        "crypto.rename_prompt": (
            "<b>🖍 Wallet name</b>\n\n"
            "Send a new name for your wallet. Default: Wallet"
        ),
        "crypto.rename_clear_btn": "🗑 Delete name",
        "crypto.rename_saved": "✅ Name updated.",
        "crypto.settings_title": "⚙️ Settings",
        "crypto.display_status": "💼 Balance display: {mode}",
        "crypto.display.all_short": "All",
        "crypto.display.min1usd_short": "Under 1 USD hidden",
        "crypto.new.title": "👛 New wallet",
        "crypto.new.subtitle": "Create or import up to 10 wallets.",
        "crypto.new.create_btn": "💎 Create new (TON)",
        "crypto.new.import_btn": "🔗 Import",
        "crypto.import.title": "🔗 Import wallet",
        "crypto.import.subtitle": "Send the seed phrase in one message",
        "crypto.import.not_found": "❌ Wallet was not found",
        "crypto.create.success_title": "✅ Wallet successfully created!",
        "crypto.create.success_subtitle": "Save your seed phrase in a safe place so you don't lose access to your wallet:",
        "crypto.create.copy_btn": "📋 Copy",
        "crypto.create.to_wallet_btn": "👛 Open wallet",
        "crypto.empty.title": "ℹ️ You don't have a wallet yet",
        "crypto.empty.subtitle": "Create one right now or import an existing one",
        "crypto.info_btn": "ℹ️ Information",
        "crypto.info.title": "ℹ️ Information",
        "crypto.info.seed_label": "seed phrase of your wallet:",
        "crypto.info.warning": "⚠️ Never forward this message to anyone! Our staff will never ask you for your wallet data.",

        # Main (Yona) menu
        "menu.yona.text": (
            '🗃 <a href="https://t.me/YonaWBot">Yona</a> - more than just a crypto wallet. '
            "Send, receive, and store crypto without leaving Telegram.\n\n"
            'Follow updates in the <a href="https://t.me/YonaWallet">official channel</a>'
        ),
        "menu.yona.wallets": "👛 Wallets",
        "menu.yona.p2p": "🅿️ P2P",
        "menu.yona.checks": "🔖 Checks",
        "menu.yona.invoices": "📝 Invoices",
        "menu.yona.subs": "📅 Subscriptions",
        "menu.yona.refs": "👨‍🌾 Referrals",
        "crypto.unlink_confirm": (
            "<b>⚠️ Really unlink this wallet?</b>\n\n"
            "If you haven't saved the seed phrase, you won't be able to restore this wallet."
        ),
        "crypto.unlinked": "Wallet unlinked",
        "crypto.no_wallet": "No wallet connected.",
        "crypto.stats.no_data": "Not enough data for statistics yet.",
        "crypto.stats.delta_up": "grew by {pct:.2f}%",
        "crypto.stats.delta_down": "fell by {pct:.2f}%",
        "crypto.stats.delta_flat": "no change",
        "crypto.stats.period_label": "Over {period}: {delta}",
        "crypto.stats.title": "📊 Statistics — {wallet}",
    },
    "uk": {
        "back": "‹ Назад",
        "cancel": "Скасувати",
        "yes": "Так",
        "no": "Ні",
        "saved": "Збережено",
        "loading": "⏳ Завантаження...",
        "in_development": "⚠️ Інтеграція в розробці.",
        "error_load_profile": "Помилка завантаження профілю. Спробуйте пізніше.",
        "error_balance_data": "Не вдалося отримати дані балансу.",
        "inline.also": "Також",
        "inline.refresh": "Оновити курс",
        "inline.title_to": "{amount} {base} → {target}",
        "inline.share_profile": "Надіслати свій профіль",
        "inline.share_profile_desc": "Текстовий профіль без фото",
        "inline.conversion_desc": "Конвертація з оновленням курсу",
        "inline.in_1_days": "за 1 день",
        "inline.in_7_days": "за 7 днів",
        "inline.in_30_days": "за 30 днів",
        "inline.refresh_throttle": "Зачекай трохи",

        "menu.add": "➕ Додати операцію",
        "menu.profile": "📊 Профіль",
        "menu.history": "🧾 Історія",
        "menu.integrations": "🔗 Інтеграції",
        "menu.refresh_gifts": "🔄 Оновити подарунки",
        "menu.settings": "⚙️ Налаштування",

        "profile.total_balance": "🏆 Загальний баланс",
        "profile.monobank": "🟢 Monobank",
        "profile.crypto": "💎 Crypto",
        "profile.gifts": "🎁 Подарунки",
        "profile.gifts_basic": "Неулучшені",

        "period.week": "Тиждень",
        "period.month": "Місяць",
        "period.year": "Рік",

        "chart.title": "Фінанси",
        "chart.income": "Доходи",
        "chart.expense": "Витрати",
        "chart.balance": "Баланс профілю",
        "chart.card_balance": "Картки",
        "chart.crypto_balance": "Крипта",
        "chart.max_income": "Макс. дохід",
        "chart.max_expense": "Макс. витрата",
        "chart.min_balance": "Мін. баланс",
        "chart.days_label": "{n} дн",
        "chart.expenses_30d": "Витрати за 30 днів",
        "chart.expense_by_category": "Витрати по категоріях — 30 днів",
        "chart.income_by_category": "Доходи по категоріях — 30 днів",
        "chart.income_vs_expense": "Доходи та витрати — 30 днів",
        "chart.no_data": "Немає даних за обраний період",
        "chart.other": "Інше",

        # History
        "history.empty": "За обраними фільтрами операцій не знайдено.",
        "history.truncated": "… (обрізано)",
        "history.page": "Стор. {n}",
        "history.filter.all": "Всі",
        "history.filter.expense": "Витрати",
        "history.filter.income": "Доходи",
        "history.filter.card": "Картка",
        "history.filter.crypto": "Крипта",
        "history.filter.big": "Великі",
        "history.filter.small": "Дрібні",

        # Integrations
        "int.title": "🔗 Інтеграції",
        "int.choose_section": "Оберіть розділ:",
        "int.history": "🧾 Історія",
        "int.connect_slot": "➕ Підключити (слот {n})",
        "int.income": "📥 Доходи",
        "int.expense": "📤 Витрати",
        "int.operations": "🧾 Операцій",
        "int.no_expenses_30d": "Немає витрат за останні 30 днів.",
        "int.account_not_found": "Акаунт не знайдено.",
        "int.unlink_confirm": "⚠️ Точно відв'язати цей акаунт?",
        "int.unlinked": "Відв'язано",
        "int.slot_limit": "Досягнуто ліміту в {n} підключень.",
        "int.unlink_yes": "✅ Так, відв'язати",
        "int.unlink_no": "❌ Скасувати",
        "int.account_card": "Картка",
        "int.action.rename": "✏️ Змінити назву",
        "int.action.unlink": "🔓 Відв'язати",
        "int.action.reset_name": "🗑 Скинути назву",
        "int.rename_prompt": "Надішліть кастомну назву для цього акаунту.",
        "int.mono_link_prompt": (
            "Введіть токен і (необов'язково) ID/PAN картки через пробіл:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "або тільки <code>&lt;token&gt;</code> для першої картки."
        ),
        "int.ton_link_prompt": "Надішліть адресу TON-гаманця одним повідомленням.",
        "int.link_failed_card": "❌ Не вдалося прив'язати картку. Спробуй ще раз.",
        "int.link_failed_wallet": "❌ Не вдалося прив'язати гаманець. Спробуй ще раз.",

        "settings.title": "⚙️ Налаштування",
        "settings.timezone": "🕒 Часовий пояс",
        "settings.timezone_label": "Часовий пояс",
        "settings.language": "🌐 Мова бота",
        "settings.language_label": "Мова",
        "settings.currency": "💱 Основна валюта",
        "settings.currency_label": "Основна валюта",
        "settings.currency_hint": "До неї будуть конвертуватись суми в першу чергу.",
        "settings.support": "💬 Написати в підтримку",
        "settings.tz_pick_hint": "Виберіть зі списку або вкажіть свій.",
        "settings.tz_custom": "✏️ Свій...",
        "settings.tz_input_prompt": (
            "Надішліть свій часовий пояс одним повідомленням.\n\n"
            "Формати:\n"
            "• IANA-ім'я: <code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>\n"
            "• UTC-зміщення: <code>+02:00</code>, <code>-05:30</code>, <code>+03</code>"
        ),
        "settings.tz_invalid": (
            "❌ Не розпізнав формат. Приклади:\n"
            "<code>Europe/Kyiv</code>, <code>Asia/Tokyo</code>, "
            "<code>+02:00</code>, <code>-05:30</code>"
        ),
        "settings.timezone_now": "Зараз: <code>{tz}</code>",
        "settings.tz_not_set": "не вказано",
        "settings.tz_custom_btn": "✏️ Свій...",

        "int.error": "Помилка",
        "int.empty_token": "Пустий токен.",
        "int.invalid_token_fmt": (
            "Невірний формат токена. Monobank токен — довгий рядок з латинських "
            "букв/цифр (≥30 символів). Отримати можна у @MonobankClientApiBot."
        ),
        "int.token_not_working": (
            "Токен не працює або Monobank API недоступний. Перевір токен і спробуй пізніше."
        ),
        "int.card_not_found": (
            "Картка/рахунок «{card}» не знайдені в цьому акаунті Monobank. "
            "Вкажи останні 4 цифри картки, або надішли лише токен (прив'яжеться перший рахунок)."
        ),
        "int.empty_address": "Пуста адреса.",
        "int.invalid_ton_addr": (
            "Невірний формат TON-адреси. Очікується 48-символьна адреса "
            "(EQ.../UQ...) або raw-форма «0:hex...»."
        ),
        "int.ton_not_found": "Адреса не знайдена в TON блокчейні.",
        "int.connected": "Підключено",
        "int.mono_link_prompt2": (
            "Введіть токен і (необов'язково) ID/PAN картки через пробіл:\n"
            "<code>&lt;token&gt; &lt;card_id&gt;</code>\n"
            "або тільки <code>&lt;token&gt;</code> для першої картки.\n\n"
            'Токен можна отримати тут: <a href="https://api.monobank.ua/index.html">api.monobank.ua</a>'
        ),

        "profile.title": "Профіль {name}",
        "profile.unimproved": "Неулучшені",
        "profile.refresh_toast": "Оновлюю подарунки...",
        "profile.custom_text_prompt": "Надішліть наступним повідомленням новий текст профілю.",
        "profile.tab.analytics": "📊 Аналітичний",
        "profile.tab.crypto": "💎 Crypto",
        "profile.crypto.title": "💎 Crypto гаманець",
        "profile.crypto.subtitle": "Імпортуйте TON-гаманці за seed-фразою або створюйте нові.",
        "profile.crypto.import_seed": "📥 Імпорт за seed-фразою",
        "profile.crypto.create_wallet": "✨ Створити TON-гаманець",
        "profile.crypto.refresh": "🔄 Оновити список",
        "profile.crypto.empty": "Поки немає імпортованих гаманців.",
        "profile.crypto.wallets": "Гаманці:",
        "profile.crypto.import_prompt": (
            "Надішліть seed-фразу та адресу гаманця одним повідомленням.\n\n"
            "Формат:\n"
            "<code>word1 word2 ... word12 | EQ...</code>"
        ),
        "profile.crypto.import_invalid": "❌ Невірний формат. Потрібні seed-фраза (12/24 слова) і TON-адреса.",
        "profile.crypto.import_saved": "✅ Гаманець імпортовано.",
        "profile.crypto.create_text": (
            "⚠️ Збережіть seed-фразу в безпечному місці. Ми покажемо її лише один раз.\n\n"
            "<code>{seed}</code>\n\n"
            "Після імпорту в TON-гаманець надішліть адресу у форматі:\n"
            "<code>address | EQ...</code>"
        ),
        "profile.crypto.create_waiting_addr": "Очікую адресу для нового гаманця.",
        "profile.crypto.create_saved": "✅ Новий гаманець створено та підключено.",
        "profile.crypto.create_invalid_addr": "❌ Невірна TON-адреса.",
        "profile.gifts_emoji_prompt": (
            "Надішліть наступним повідомленням маппінг подарунків.\n"
            "Формат кожного рядка (емодзі на початку):\n"
            "<code>🐰 https://t.me/nft/JellyBunny-57735</code>\n"
            "або просто: <code>🐰 JellyBunny</code>"
        ),
        "profile.gifts_emoji_saved": "✅ Збережено:",
        "profile.gifts_emoji_invalid": "Нічого не розпізнав. Перевір формат.",
        "profile.basic_gifts_prompt": (
            "Надішліть списком зміни неулучшених подарунків.\n"
            "Додати/змінити ціну:\n"
            "<code>Назва, ID, ціна_usd</code>\n"
            "Видалити зі списку:\n"
            "<code>del, ID</code>"
        ),
        "profile.basic_gifts_saved": "✅ Збережено. Оновлено/додано: {changed}, видалено: {removed}.",
        "profile.basic_gifts_invalid": "⚠️ Не розпізнані рядки:",

        "tx.invalid_input": "{err}\n\nПриклад: -250 грн їжа обід",
        "tx.saved": "Операцію збережено: {tx_type} {amount} {currency} [{category}] {desc}",
        "tx.integrations_intro": "Керування інтеграціями:",

        "tz.kyiv": "🇺🇦 Київ",
        "tz.moscow": "🇷🇺 Москва",
        "tz.minsk": "🇧🇾 Мінськ",
        "tz.warsaw": "🇵🇱 Варшава",
        "tz.tashkent": "🇺🇿 Ташкент",
        "tz.london": "🇬🇧 Лондон",
        "tz.berlin": "🇩🇪 Берлін",
        "tz.newyork": "🇺🇸 Нью-Йорк",

        # Crypto main screen buttons
        "crypto.deposit": "📥 Поповнити",
        "crypto.deposit.title": "📥 Поповнення",
        "crypto.deposit.body": (
            "Для поповнення балансу вашого гаманця в мережі <b>TON</b>, "
            "надішліть монети на адресу нижче:\n"
            "Адреса: <code>{addr}</code>\n\n"
            "Мінімальна сума: <b>0.0001 TON</b> у будь-якій монеті.\n\n"
            "⚠️ Увага! Поповнюйте тільки монетами в мережі <b>TON</b>. "
            "Якщо ви використовуєте іншу мережу, ваші монети <b>буде втрачено</b>."
        ),
        "crypto.deposit.show_qr": "Показати QR-код",
        "crypto.deposit.hide_qr": "Сховати QR-код",
        "crypto.deposit.no_payment": "Не прийшов депозит?",
        "crypto.deposit.help_title": "Не прийшов депозит?",
        "crypto.deposit.help_body": (
            "Можливо, переказ було виконано зі смарт-контракту замість гаманця, "
            "тому переконайтеся, що кошти успішно прийшли на ваш гаманець у "
            "блокчейні.\n\n"
            "Після цього натисніть «Перевірити депозит». Якщо депозит так і не "
            "прийшов — зв'яжіться з підтримкою."
        ),
        "crypto.deposit.check_btn": "🔍 Перевірити депозит",
        "crypto.deposit.support_btn": "💬 Зв'язатися з підтримкою",
        "crypto.deposit.check_in_progress": "Перевіряю…",
        "crypto.deposit.check_done": "Баланс оновлено",
        "crypto.withdraw": "📤 Вивести",
        "crypto.configure": "⚙️ Налаштувати",
        "crypto.stats": "📊 Статистика",
        "crypto.my_wallet": "Гаманець",
        "crypto.deposit_addr_label": "📥 Адреса для поповнення",
        "crypto.display_btn": "💼 Відображення балансів",
        "crypto.rename_btn": "🖍 Перейменувати",
        "crypto.unlink_btn": "⛓️‍💥 Відв'язати гаманець",
        "crypto.display_title": "💼 Відображення балансів",
        "crypto.display_subtitle": "Оберіть як відображати баланс Вашого гаманця:",
        "crypto.display.all": "Показувати всі",
        "crypto.display.nonzero": "Приховувати нульові",
        "crypto.display.min1usd": "Приховувати менше 1 USD",
        "crypto.rename_prompt": (
            "<b>🖍 Назва гаманця</b>\n\n"
            "Надішліть нову назву вашого гаманця для зручного відображення в боті. "
            "За замовчуванням: Гаманець"
        ),
        "crypto.rename_clear_btn": "🗑 Видалити назву",
        "crypto.rename_saved": "✅ Назву оновлено.",
        "crypto.settings_title": "⚙️ Налаштування",
        "crypto.display_status": "💼 Відображення балансів: {mode}",
        "crypto.display.all_short": "Усі",
        "crypto.display.min1usd_short": "Менше 1 USD приховано",
        "crypto.new.title": "👛 Новий гаманець",
        "crypto.new.subtitle": "Створіть або імпортуйте до 10 гаманців.",
        "crypto.new.create_btn": "💎 Створити новий (TON)",
        "crypto.new.import_btn": "🔗 Імпортувати",
        "crypto.import.title": "🔗 Імпортувати гаманець",
        "crypto.import.subtitle": "Надішліть seed-фразу одним повідомленням",
        "crypto.import.not_found": "❌ Гаманець не знайдено",
        "crypto.create.success_title": "✅ Гаманець успішно створено!",
        "crypto.create.success_subtitle": "Збережіть seed-фразу в надійному місці, щоб не втратити доступ до гаманця:",
        "crypto.create.copy_btn": "📋 Скопіювати",
        "crypto.create.to_wallet_btn": "👛 До гаманця",
        "crypto.empty.title": "ℹ️ У вас ще немає гаманця",
        "crypto.empty.subtitle": "Створіть його прямо зараз або імпортуйте вже існуючий",
        "crypto.info_btn": "ℹ️ Інформація",
        "crypto.info.title": "ℹ️ Інформація",
        "crypto.info.seed_label": "seed-фраза вашого гаманця:",
        "crypto.info.warning": "⚠️ Нікому не пересилайте це повідомлення! Наші співробітники ніколи не попросять дані від ваших гаманців.",

        # Main (Yona) menu
        "menu.yona.text": (
            '🗃 <a href="https://t.me/YonaWBot">Yona</a> - більше ніж просто криптогаманець. '
            "Надсилайте, отримуйте та зберігайте криптовалюту не виходячи з Telegram.\n\n"
            'Дізнавайтесь усі новини в <a href="https://t.me/YonaWallet">офіційному каналі</a>'
        ),
        "menu.yona.wallets": "👛 Гаманці",
        "menu.yona.p2p": "🅿️ P2P",
        "menu.yona.checks": "🔖 Чеки",
        "menu.yona.invoices": "📝 Рахунки",
        "menu.yona.subs": "📅 Підписки",
        "menu.yona.refs": "👨‍🌾 Реферали",
        "crypto.unlink_confirm": (
            "<b>⚠️ Точно відв'язати цей гаманець?</b>\n\n"
            "Якщо ви не зберегли seed-фразу, ви не зможете повернути цей гаманець."
        ),
        "crypto.unlinked": "Гаманець відв'язано",
        "crypto.no_wallet": "Немає підключеного гаманця.",
        "crypto.stats.no_data": "Поки недостатньо даних для статистики.",
        "crypto.stats.delta_up": "зросло на {pct:.2f}%",
        "crypto.stats.delta_down": "впало на {pct:.2f}%",
        "crypto.stats.delta_flat": "без змін",
        "crypto.stats.period_label": "За {period}: {delta}",
        "crypto.stats.title": "📊 Статистика — {wallet}",
    },
}

_DEFAULT_LANG = "ru"
_SUPPORTED_LANGS = set(_TRANSLATIONS.keys())


def supported_languages() -> set[str]:
    return set(_SUPPORTED_LANGS)


def t(key: str, lang: str | None = None, /, **fmt) -> str:
    """Translate `key` to `lang`. Falls back: lang → ru → key itself."""
    code = (lang or _DEFAULT_LANG).lower()
    if code not in _TRANSLATIONS:
        code = _DEFAULT_LANG
    s = _TRANSLATIONS[code].get(key)
    if s is None:
        s = _TRANSLATIONS[_DEFAULT_LANG].get(key, key)
    if fmt:
        try:
            return s.format(**fmt)
        except (KeyError, IndexError):
            return s
    return s


async def get_user_lang(db, telegram_id: int) -> str:
    """Resolves the user's preferred language. Defaults to ru if not stored."""
    from sqlalchemy import select  # local to avoid module-level coupling
    from app.db.models import User

    row = (
        await db.execute(
            select(User.language).where(User.telegram_id == telegram_id).limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return _DEFAULT_LANG
    code = str(row).lower()
    return code if code in _SUPPORTED_LANGS else _DEFAULT_LANG
