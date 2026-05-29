import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import html
import re
from time import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import and_, select

from app.bot.keyboards import (
    crypto_create_success_keyboard,
    crypto_deposit_help_keyboard,
    crypto_deposit_keyboard,
    crypto_display_mode_keyboard,
    crypto_empty_keyboard,
    crypto_import_prompt_keyboard,
    crypto_info_keyboard,
    crypto_info_with_copy_keyboard,
    crypto_main_keyboard,
    crypto_reorder_keyboard,
    crypto_send_addr_keyboard,
    crypto_send_amount_keyboard,
    crypto_send_coins_keyboard,
    crypto_send_confirm_keyboard,
    crypto_send_memo_keyboard,
    crypto_send_processing_keyboard,
    crypto_new_wallet_menu_keyboard,
    crypto_rename_keyboard,
    crypto_settings_keyboard,
    crypto_unlink_confirm_keyboard,
    profile_crypto_keyboard,
    profile_period_keyboard,
)
from app.bot.panel import push_photo_panel, push_text_panel
from app.db.models import Account, AccountType, BasicGiftItem, Currency, ProfileSnapshot
from app.db.session import SessionLocal
from app.i18n import t
from app.services.fx_service import FxService
from app.services.ledger_service import LedgerService
from app.services.monobank_service import MonobankService
from app.services.report_service import ReportService
from app.services.security import SecretCipher
from app.services.ton_service import TonService
from app.services.gift_satellite_service import GiftSatelliteService

router = Router(name="profile")
ledger = LedgerService()
report_service = ReportService()
fx_service = FxService()
monobank_service = MonobankService()
ton_service = TonService()
gift_service = GiftSatelliteService()
_profile_snapshot_cache: dict[int, dict] = {}
_gift_items_cache: dict[int, tuple[list[tuple[str, Decimal]], float]] = {}  # (items, priced_at)
_gift_slugs_cache: dict[int, list[str]] = {}  # only refreshed via button
_gift_emoji_map: dict[str, str] = {}  # collection name prefix → emoji
_pending_gift_emoji_update: set[int] = set()
_gift_sync_in_progress: set[int] = set()  # users currently doing first-time gift sync
_price_refresh_in_progress: set[int] = set()  # users currently doing background price refresh
_pending_basic_gifts_update: set[int] = set()
_profile_view_by_user: dict[int, str] = {}
_pending_crypto_seed_import: set[int] = set()
_pending_crypto_create_addr: dict[int, str] = {}
_current_wallet_idx: dict[int, int] = {}
_current_coin_page: dict[int, int] = {}
_balance_display_mode: dict[int, str] = {}  # user_id → "all" | "min1usd"
_pending_crypto_rename: dict[int, int] = {}  # user_id → wallet account_id
_pending_crypto_seed_only: set[int] = set()  # user_id → awaiting seed-only import
_pending_crypto_addr_only: set[int] = set()  # user_id → awaiting address-only import
_last_generated_seed: dict[int, str] = {}    # user_id → just-generated seed (for 📋 copy button)
# Кэш цен в USD за единицу монеты. Используется как fallback, когда API
# временно не возвращает котировку, — чтобы стоимость в скобках не пропадала.
# Ключ — символ монеты в верхнем регистре.
_last_unit_price_usd: dict[str, Decimal] = {}
# Промежуточное состояние свапа в экране переупорядочивания.
# user_id → выбранный account_id (ждём №-нажатие) или None.
_pending_reorder_pick: dict[int, int] = {}

# Send-flow state. Один объект на юзера, на каждом шаге обновляется.
# Поля: wallet_id, symbol, balance, min_amt, fee, amount, address, memo, step.
_send_state: dict[int, dict] = {}
_send_coin_page: dict[int, int] = {}
_pending_send_amount: set[int] = set()
_pending_send_addr: set[int] = set()
_pending_send_memo: set[int] = set()

# Минимальная сумма перевода на TON-сети — фикс. константа в TON, остальные
# монеты конвертим по их usd_price.
SEND_MIN_TON = Decimal("0.0000001")
# Комиссия — фиксированный $0.2 в эквиваленте, конвертим в монету по её курсу.
SEND_FEE_USD = Decimal("0.2")

COINS_PER_PAGE = 8
COIN_LINKS: dict[str, str] = {
    "TON": "https://ton.org/",
    "USDT": "https://tether.to/",
}
_TON_RAW_RE = re.compile(r"^-?\d+:[a-fA-F0-9]{64}$")
_TON_FRIENDLY_RE = re.compile(r"^[A-Za-z0-9_-]{48}$")
_cipher = SecretCipher()

_DEFAULT_BASIC_GIFTS: list[tuple[str, str, Decimal]] = [
    ("Trojan Horse", "5956308547863052791", Decimal("160")),
    ("Telegram Pin", "5818708013426410448", Decimal("12500")),
    ("Mask", "5775966332847654507", Decimal("12")),
    ("Coffin", "5776227780391864916", Decimal("40")),
    ("Grave", "5775955135867913556", Decimal("140")),
    ("Durov`s Statuette", "6003477390536213997", Decimal("3000")),
    ("Sneakers", "6001229799790478558", Decimal("150")),
    ("T-shirt", "6001425315291727333", Decimal("125")),
    ("Pencil", "5882129648002794519", Decimal("25")),
    ("Case", "5884080014126745057", Decimal("80")),
    ("Coconut Drink", "5832371318007268701", Decimal("4.5")),
    ("Sand Castle", "5834918435477259676", Decimal("10")),
    ("Surfboard", "5832497899283415733", Decimal("50")),
    ("REDO", "5832279504491381684", Decimal("500")),
    ("Durov Glasses", "5834651202612102354", Decimal("500")),
    ("Heart Pendant", "5872744075014177223", Decimal("50")),
    ("Lamp Candle", "5913351908466098791", Decimal("15")),
    ("Eight Roses", "5933770397739647689", Decimal("15")),
    ("Easter Cake", "5773791997064119815", Decimal("8")),
    ("1 May", "5807641025165919973", Decimal("6")),
    ("Bird Mark", "5832325860073407546", Decimal("8")),
    ("Red Star", "5830323722413671504", Decimal("10")),
    ("Cream IceCream", "5897607679345427347", Decimal("8")),
    ("Statue of Liberty", "5999298447486747746", Decimal("7")),
]


def _schedule_price_refresh(
    *,
    bot: Bot,
    user_id: int,
    telegram_id: int,
    chat_id: int,
    panel_user_id: int,
    username: str | None,
    period: str,
) -> None:
    """
    Background task: re-compute prices for cached slugs, preserving previous prices
    as fallback for slugs the API can't price right now. Persists results to DB so
    the next bot restart shows real data immediately.
    """
    if user_id in _price_refresh_in_progress:
        return
    cached_slugs = _gift_slugs_cache.get(user_id)
    if not cached_slugs:
        return
    _price_refresh_in_progress.add(user_id)

    async def _bg() -> None:
        try:
            previous_entry = _gift_items_cache.get(user_id)
            previous_prices: dict[str, Decimal] = (
                dict(previous_entry[0]) if previous_entry else {}
            )
            _, items = await gift_service.calculate_external_gifts_value(
                cached_slugs, previous_prices=previous_prices
            )
            _gift_items_cache[user_id] = (items, time())
            _profile_snapshot_cache.pop(user_id, None)
            # Persist for restart resilience.
            try:
                async with SessionLocal() as bg_db:
                    await gift_service.persist_items(bg_db, user_id, items)
            except Exception:
                pass
            await render_profile(
                bot=bot,
                chat_id=chat_id,
                panel_user_id=panel_user_id,
                telegram_id=telegram_id,
                username=username,
                period=period,
            )
        except Exception:
            pass
        finally:
            _price_refresh_in_progress.discard(user_id)

    asyncio.create_task(_bg())


def _schedule_full_gift_sync(
    *,
    bot: Bot,
    user_id: int,
    telegram_id: int,
    chat_id: int,
    panel_user_id: int,
    username: str | None,
    period: str,
) -> None:
    """First-ever sync: scrape slugs from Telegram + compute prices."""
    if user_id in _gift_sync_in_progress:
        return
    _gift_sync_in_progress.add(user_id)

    async def _bg() -> None:
        try:
            async with SessionLocal() as bg_db:
                _, items_bg = await gift_service.sync_gifts_balance(
                    db=bg_db, bot=bot, user_id=user_id, telegram_id=telegram_id,
                )
                # Persist for restart resilience.
                try:
                    await gift_service.persist_items(bg_db, user_id, items_bg)
                except Exception:
                    pass
            _gift_slugs_cache[user_id] = [s for s, _ in items_bg]
            _gift_items_cache[user_id] = (items_bg, time())
            _profile_snapshot_cache.pop(user_id, None)
            await render_profile(
                bot=bot,
                chat_id=chat_id,
                panel_user_id=panel_user_id,
                telegram_id=telegram_id,
                username=username,
                period=period,
            )
        except Exception:
            pass
        finally:
            _gift_sync_in_progress.discard(user_id)

    asyncio.create_task(_bg())
BALANCE_CACHE_TTL_SECONDS = 60
GIFT_PRICE_TTL_SECONDS = 30 * 60  # 30 min
_pending_profile_text_update: set[int] = set()
_custom_profile_text: dict[int, str] = {}


def _shorten(s: str | None) -> str:
    if not s:
        return "..."
    if len(s) <= 12:
        return s
    return f"{s[:5]}...{s[-4:]}"


_AUTO_PAN_LABEL_RE = re.compile(r"^\d{4}\.\.\.\d{4}$")
# Stale shortened-ref pattern, e.g. "VsMOl...sNg3" — has letters, NOT a real PAN.
_STALE_REF_LABEL_RE = re.compile(r"^[A-Za-z0-9]{3,}\.\.\.[A-Za-z0-9]{3,}$")


def _is_legacy_default(name: str | None) -> bool:
    """True for empty / 'Monobank card' / 'TON wallet'."""
    return not name or name in {"Monobank card", "TON wallet"}


def _is_stale_auto_label(name: str | None) -> bool:
    """
    True if the name looks like an auto-generated label that we should refresh
    from API: legacy defaults OR a shortened API-ref like 'VsMOl...sNg3'.
    A real masked PAN like '4441...5985' is NOT considered stale.
    """
    if _is_legacy_default(name):
        return True
    if _AUTO_PAN_LABEL_RE.match(name):
        return False  # real PAN, leave alone
    if _STALE_REF_LABEL_RE.match(name):
        return True   # shortened ref → stale
    return False


def _is_custom_user_name(name: str | None) -> bool:
    """True if the user has actually set a custom display name."""
    if _is_legacy_default(name):
        return False
    if _AUTO_PAN_LABEL_RE.match(name):
        return False
    if _STALE_REF_LABEL_RE.match(name):
        return False
    return True


def _mono_display_label(acc, pan_label: str | None, lang: str = "ru") -> str:
    """Pick label for a Monobank card row: custom name → PAN → localized 'Card'."""
    if _is_custom_user_name(acc.display_name):
        return acc.display_name
    if pan_label:
        return pan_label
    if _AUTO_PAN_LABEL_RE.match(acc.display_name or ""):
        return acc.display_name
    from app.i18n import t as _t
    return _t("int.account_card", lang)


def _ton_display_label(acc) -> str:
    """Pick label for a TON wallet row: custom name → shortened address."""
    if _is_custom_user_name(acc.display_name):
        return acc.display_name
    return _shorten(acc.external_ref)


_PERIOD_DAYS = {"week": 7, "month": 30, "year": 365}


def _is_valid_ton_address(address: str) -> bool:
    a = (address or "").strip()
    return bool(_TON_RAW_RE.match(a) or _TON_FRIENDLY_RE.match(a))


def _looks_like_seed_phrase(seed: str) -> bool:
    words = [w for w in seed.strip().split() if w]
    return len(words) in {12, 24}


def _generate_seed_phrase(word_count: int = 24) -> str:
    """TON wallets use their own 24-word mnemonic scheme (not standard BIP39)."""
    try:
        from tonsdk.crypto import mnemonic_new
        return " ".join(mnemonic_new(24))
    except Exception:
        # Fallback only — should never trigger in production.
        from mnemonic import Mnemonic
        return Mnemonic("english").generate(strength=256)


def _get_v5r1_code_cell():
    """
    Достаём Cell кода контракта V5R1 из pytoniq, перебирая разные
    layout'ы и способы. Возвращаем Cell или None.
    """
    from pytoniq_core import Cell
    import inspect

    # 1) Сначала ищем класс WalletV5R1
    cls = None
    src_module = None
    for path in (
        "pytoniq.contract.wallets.wallet_v5",      # pytoniq 0.1.43
        "pytoniq.contract.wallets.v5r1",
        "pytoniq.contract.wallets.wallet_v5r1",
        "pytoniq.contract.wallets.wallet",
        "pytoniq.contract.wallets",
        "pytoniq",
    ):
        try:
            m = __import__(path, fromlist=["WalletV5R1"])
            if hasattr(m, "WalletV5R1"):
                cls = m.WalletV5R1
                src_module = m
                break
        except Exception:
            continue
    if cls is None:
        return None

    # 2) Пробуем разные атрибуты — может быть классовый Cell, может property
    for attr in ("code", "CODE", "_code", "WALLET_V5_CODE", "WALLET_V5R1_CODE"):
        val = getattr(cls, attr, None)
        if isinstance(val, Cell):
            return val
        # property/method — вызываем без аргументов
        if callable(val):
            try:
                r = val()
                if isinstance(r, Cell):
                    return r
            except Exception:
                pass

    # 3) Ищем константу в модуле — Cell или hex/base64 строку с кодом
    for name in dir(src_module):
        try:
            val = getattr(src_module, name)
        except Exception:
            continue
        if isinstance(val, Cell):
            return val
        if isinstance(val, str) and (name.upper().startswith("WALLET") or "V5" in name.upper()):
            try:
                if val.startswith("b5ee9c") or val.startswith("B5EE9C"):
                    return Cell.one_from_boc(bytes.fromhex(val))
                # base64?
                import base64
                return Cell.one_from_boc(base64.b64decode(val))
            except Exception:
                continue

    # 4) Парсим исходник класса — ищем .one_from_boc(...) вызов с константой
    try:
        src = inspect.getsource(cls)
        import re
        m = re.search(r"one_from_boc\(\s*['\"]([A-Za-z0-9+/=]+)['\"]", src)
        if m:
            raw = m.group(1)
            try:
                return Cell.one_from_boc(bytes.fromhex(raw))
            except Exception:
                import base64
                return Cell.one_from_boc(base64.b64decode(raw))
    except Exception:
        pass

    return None


async def _derive_v5r1_address(seed_words: list[str]) -> str | None:
    """
    V5R1 (W5) деривация. Полностью обходим pytoniq.from_data (он зовёт
    провайдер) и строим state_init руками из публично доступных кусков:
      • pub_key через tonsdk (т.к. TON-native seed)
      • WALLET_V5_R1_CODE — модульная константа в pytoniq.contract.wallets.wallet_v5
      • create_data_cell — статический метод на WalletV5R1, строит data cell
        со всеми тонкостями (wallet_id, signature_allowed)
      • StateInit из pytoniq_core.tlb.account собирает кодом + данными
      • Хеш state_init → Address

    Возвращаем UQ-формат (non-bounceable), как сохраняет Tonkeeper.
    """
    import logging
    log = logging.getLogger(__name__)

    # 1. pub_key
    try:
        from tonsdk.crypto import mnemonic_to_wallet_key
        pub_key, _ = mnemonic_to_wallet_key(seed_words)
        if not (isinstance(pub_key, (bytes, bytearray)) and len(pub_key) == 32):
            return None
        pub_key = bytes(pub_key)
    except Exception as exc:
        log.warning("V5R1: tonsdk pub_key failed: %s", exc)
        return None

    # 2. WALLET_V5_R1_CODE + WalletV5R1.create_data_cell
    WalletV5R1 = None
    CODE = None
    for path in (
        "pytoniq.contract.wallets.wallet_v5",
        "pytoniq.contract.wallets.v5r1",
        "pytoniq.contract.wallets.wallet_v5r1",
        "pytoniq.contract.wallets",
    ):
        try:
            m = __import__(path, fromlist=["WalletV5R1", "WALLET_V5_R1_CODE"])
            if hasattr(m, "WalletV5R1"):
                WalletV5R1 = m.WalletV5R1
            if hasattr(m, "WALLET_V5_R1_CODE"):
                CODE = m.WALLET_V5_R1_CODE
            if WalletV5R1 is not None and CODE is not None:
                break
        except Exception:
            continue
    if WalletV5R1 is None or CODE is None:
        log.warning("V5R1: WalletV5R1/CODE not found in pytoniq")
        return None

    # 3. data_cell через статический метод (он сам разбирается с wallet_id)
    try:
        data_cell = WalletV5R1.create_data_cell(
            public_key=pub_key,
            wc=0,
            network_global_id=-239,
            subwallet_number=0,
            is_signature_allowed=True,
        )
    except Exception as exc:
        log.warning("V5R1: create_data_cell failed: %s", exc)
        return None

    # 4. StateInit → Address. Перебираем разные API.
    try:
        from pytoniq_core.tlb.account import StateInit
        from pytoniq_core.boc.address import Address
        st = StateInit(code=CODE, data=data_cell)
        # Получаем хеш — у разных версий разный API
        addr_hash = None
        for getter in (
            lambda: st.serialize().hash,
            lambda: st.serialize().hash(),
            lambda: st.cell.hash,
            lambda: st.to_cell().hash,
        ):
            try:
                v = getter()
                if isinstance(v, (bytes, bytearray)):
                    addr_hash = bytes(v)
                    break
            except Exception:
                continue
        if addr_hash is None:
            log.warning("V5R1: cannot extract state_init hash")
            return None
        address = Address((0, addr_hash))
        # UQ-формат (non-bounceable) — как у Tonkeeper по умолчанию
        return address.to_str(
            is_user_friendly=True, is_bounceable=False, is_url_safe=True,
        )
    except Exception as exc:
        log.warning("V5R1: StateInit/Address build failed: %s", exc)
        return None


async def _derive_ton_addresses(seed: str) -> list[str]:
    """
    Возвращает СПИСОК адресов-кандидатов из одной seed-фразы под разные
    версии TON-кошельков: V5R1 (W5), V4R2, V3R2, V4R1. + BIP39 фоллбэк.
    Дубликаты убираются, порядок сохраняется.
    """
    import logging
    log = logging.getLogger(__name__)
    words = [w.strip().lower() for w in (seed or "").split() if w.strip()]
    if not words:
        return []

    seen: set[str] = set()
    out: list[str] = []

    def _add(addr: str | None) -> None:
        if addr and addr not in seen:
            out.append(addr)
            seen.add(addr)

    # 1) V5R1 (W5) — первая в порядке, так как новый дефолт большинства кошельков
    if len(words) == 24:
        _add(await _derive_v5r1_address(words))

    # 2) Старые версии через tonsdk (24 слова, TON-native mnemonic)
    if len(words) == 24:
        try:
            from tonsdk.contract.wallet import Wallets, WalletVersionEnum
            for ver in (WalletVersionEnum.v4r2, WalletVersionEnum.v3r2,
                        WalletVersionEnum.v4r1):
                try:
                    _, _, _, wallet = Wallets.from_mnemonics(words, ver, 0)
                    _add(wallet.address.to_string(True, True, True))
                except Exception as exc:
                    log.info("Derivation %s failed: %s", ver, exc)
        except Exception as exc:
            log.info("tonsdk import failed: %s", exc)

    # 3) BIP39 fallback (12/15/18/21/24)
    if len(words) in {12, 15, 18, 21, 24}:
        try:
            from bip_utils import (
                Bip39MnemonicValidator,
                Bip39SeedGenerator,
                Bip44,
                Bip44Coins,
            )
            phrase = " ".join(words)
            if Bip39MnemonicValidator().IsValid(phrase):
                seed_bytes = Bip39SeedGenerator(phrase).Generate()
                ton_acc = Bip44.FromSeed(seed_bytes, Bip44Coins.TON).DeriveDefaultPath()
                _add(ton_acc.PublicKey().ToAddress())
        except Exception as exc:
            log.info("BIP39→TON derivation failed: %s", exc)

    return out


async def _pick_active_ton_address(candidates: list[str]) -> str | None:
    """
    Из списка адресов-кандидатов выбирает тот, что реально существует на
    блокчейне (active или balance > 0). Если ни один не активен — возвращает
    None (вызывающий код должен предложить юзеру вставить адрес вручную).
    """
    if not candidates:
        return None
    import httpx
    from app.config import get_settings
    base = get_settings().ton_api_url
    async with httpx.AsyncClient(timeout=10) as client:
        for addr in candidates:
            try:
                r = await client.get(f"{base}/blockchain/accounts/{addr}")
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("status", "")
                    bal = int(data.get("balance", 0))
                    if status == "active" or bal > 0:
                        return addr
            except Exception:
                continue
    return None


def _derive_ton_address(seed: str) -> str | None:
    """
    Синхронная деривация дефолтного W5R1-адреса (как у Tonkeeper / @wallet).
    Для свежесозданного кошелька он ещё не активирован — берём именно W5R1,
    т.к. этот формат ожидают современные клиенты при первом импорте.
    """
    words = [w.strip().lower() for w in (seed or "").split() if w.strip()]
    if len(words) != 24:
        return None
    try:
        from tonutils.contracts.wallet.versions.v5 import WalletV5R1
        from tonutils.clients import ToncenterClient
        from ton_core.contrib.types import NetworkGlobalID
        client = ToncenterClient(network=NetworkGlobalID.MAINNET)
        wallet, _, _, _ = WalletV5R1.from_mnemonic(client, words, validate=False)
        return wallet.address.to_str(
            is_user_friendly=True, is_bounceable=False, is_url_safe=True,
        )
    except Exception:
        pass
    # Фоллбэк — V4R2 через tonsdk (если tonutils по какой-то причине не загрузился).
    try:
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum
        _, _, _, wallet = Wallets.from_mnemonics(words, WalletVersionEnum.v4r2, 0)
        return wallet.address.to_string(True, True, True)
    except Exception:
        return None


async def _ensure_first_wallet(db, user_id: int) -> None:
    """Auto-create one wallet on the very first /start so the UI is never empty."""
    from sqlalchemy import select as _select
    from app.db.models import Account as _Account
    ever_had = (
        await db.execute(
            _select(_Account.id)
            .where(_Account.user_id == user_id, _Account.account_type == AccountType.TON_WALLET)
            .limit(1)
        )
    ).scalar_one_or_none()
    if ever_had:
        return
    seed = _generate_seed_phrase(24)
    address = _derive_ton_address(seed)
    if not address:
        return
    account = await ton_service.link_wallet(db, user_id, address)
    account.encrypted_secret = _cipher.encrypt(seed)
    account.is_favorite = True
    account.sort_order = 1
    await db.commit()


def _wallet_display_name(acc, lang: str) -> str:
    """Bare wallet name (no emoji, no address) — uses custom name or i18n default."""
    if _is_custom_user_name(acc.display_name):
        return acc.display_name
    return t("crypto.my_wallet", lang)


def _coin_emoji(symbol: str | None) -> str:
    """Эмодзи для монеты в тексте: 💎 TON, 💵 USDT/USDC, 🪙 остальные."""
    s = (symbol or "").upper()
    if s == "TON":
        return "💎"
    if s in {"USDT", "USDC", "USD₮", "DAI", "BUSD", "TUSD"}:
        return "💵"
    return "🪙"


def _format_coin_amount(amount: Decimal) -> str:
    """Format coin balance: trim trailing zeros, keep up to 9 significant decimals."""
    q = amount.quantize(Decimal("0.000000001"))
    s = format(q, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _convert_usd(amount_usd: Decimal, base_ccy: str, uah_per_usd: Decimal | None) -> tuple[Decimal, str]:
    """Backwards-compatible: convert USD to UAH if available, else stays USD."""
    ccy = (base_ccy or "USD").upper()
    if ccy == "UAH" and uah_per_usd:
        return amount_usd * uah_per_usd, "UAH"
    return amount_usd, "USD"


async def _get_base_per_usd(base_ccy: str) -> Decimal | None:
    """Сколько единиц base_ccy за 1 USD. Использует тот же источник, что и инлайн-конвертер."""
    ccy = (base_ccy or "USD").upper()
    if ccy == "USD":
        return Decimal("1")
    # Импорт здесь чтобы не плодить циклы (transactions импортит profile тоже).
    from app.bot.handlers.transactions import _usd_rate_for
    try:
        usd_per_ccy = await _usd_rate_for(ccy)
    except Exception:
        usd_per_ccy = None
    if usd_per_ccy is None or usd_per_ccy <= 0:
        return None
    return Decimal("1") / Decimal(str(usd_per_ccy))


# Знаки валют для коротких подписей в скобках балансов.
_CCY_SYMBOL: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "UAH": "₴", "RUB": "₽", "BYN": "Br", "PLN": "zł", "UZS": "сум",
    "KZT": "₸", "TRY": "₺", "TON": "TON",
}


def _convert_usd_to_base(
    amount_usd: Decimal, base_ccy: str, base_per_usd: Decimal | None,
) -> tuple[Decimal, str]:
    """Конвертирует USD в base_ccy. При None курсе — фоллбэк в USD."""
    if base_per_usd is None:
        return amount_usd, "USD"
    ccy = (base_ccy or "USD").upper()
    return amount_usd * base_per_usd, ccy


def _ccy_short(label: str) -> str:
    """Короткий тег валюты для отображения в скобках: '$', '₴', 'EUR' и т.п."""
    return _CCY_SYMBOL.get(label.upper(), label.upper())


async def _render_deposit_screen(
    *,
    bot,
    chat_id: int,
    uid: int,
    uname: str | None,
    callback,
    qr_shown: bool,
) -> None:
    """
    Рисует экран пополнения. Если qr_shown=True — генерим QR и шлём как
    link preview через наш веб-сервис, иначе показываем только текст.
    """
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"
        accounts = await ledger.get_active_accounts_by_type(
            db, user.id, AccountType.TON_WALLET,
        )

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass

    if not accounts:
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    idx = _current_wallet_idx.get(uid, 0)
    if idx < 0 or idx >= len(accounts):
        idx = 0
    acc = accounts[idx]
    addr = acc.external_ref or ""

    text = (
        f"<b>{t('crypto.deposit.title', lang)}</b>\n\n"
        + t("crypto.deposit.body", lang).format(addr=html.escape(addr))
    )

    keyboard = crypto_deposit_keyboard(lang, qr_shown=qr_shown)

    qr_url: str | None = None
    if qr_shown:
        try:
            from app.services.qr_service import render_wallet_qr
            from app.bot.handlers.transactions import _upload_card_to_web
            png_bytes = render_wallet_qr(addr)
            qr_url = await _upload_card_to_web(
                png_bytes,
                title=f"QR — {addr[:8]}…",
                description="TON deposit QR",
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("QR render failed: %s", exc)
            qr_url = None

    if qr_url:
        # Шлём через push_text_panel с link_preview — превью с QR появится сверху.
        from aiogram.types import LinkPreviewOptions
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(
                url=qr_url, prefer_large_media=True, show_above_text=True,
            ),
        )
    else:
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=keyboard,
            parse_mode="HTML",
            disable_web_preview=True,
        )


async def render_crypto_main(
    *,
    force_new: bool = False,
    bot: Bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
    wallet_idx: int | None = None,
    coin_page: int | None = None,
) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = getattr(user, "language", "ru") or "ru"
        base_ccy = user.base_currency.value if user.base_currency else "USD"
        accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)

    # Курс в базовую валюту юзера — для конвертации общих и поштучных сумм.
    base_per_usd = await _get_base_per_usd(base_ccy)

    wallets = [(acc.id, _ton_display_label(acc)) for acc in accounts]

    if not accounts:
        # Dedicated empty state — no wallet at all.
        text = (
            f"<b>{t('crypto.empty.title', lang)}</b>\n\n"
            f"{t('crypto.empty.subtitle', lang)}"
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=panel_user_id,
            text=text,
            reply_markup=crypto_empty_keyboard(lang),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        _profile_view_by_user[panel_user_id] = "crypto"
        return

    if wallet_idx is None:
        wallet_idx = _current_wallet_idx.get(panel_user_id, 0)
    wallet_idx = max(0, min(wallet_idx, len(wallets) - 1))
    _current_wallet_idx[panel_user_id] = wallet_idx
    _profile_view_by_user[panel_user_id] = "crypto"

    display_mode = _balance_display_mode.get(panel_user_id, "all")
    coins: list[dict] = []  # [{symbol, amount, usd_value, always_show}]
    if accounts:
        acc = accounts[wallet_idx]
        try:
            ton_bal_task = ton_service.get_live_balance_ton(acc)
            jets_task = ton_service.get_jettons_detailed(acc)
            price_task = ton_service.ton_price_usd()
            ton_bal, jets, ton_price = await asyncio.gather(ton_bal_task, jets_task, price_task)
        except Exception:
            ton_bal, jets, ton_price = None, [], None

        ton_amount = ton_bal if ton_bal is not None else Decimal("0")
        # Если API вернул свежую цену TON — кэшируем за единицу для будущих
        # фоллбэков. Иначе пытаемся взять прошлую известную и хотя бы
        # приблизительно показать сумму — пользователю важнее увидеть какие-то
        # цифры, чем пустую скобку.
        if ton_price is not None:
            _last_unit_price_usd["TON"] = ton_price
            ton_usd = ton_amount * ton_price
        elif ton_amount == 0:
            ton_usd = Decimal("0")
        elif "TON" in _last_unit_price_usd:
            ton_usd = ton_amount * _last_unit_price_usd["TON"]
        else:
            ton_usd = None
        coins.append({
            "symbol": "TON",
            "amount": ton_amount,
            "usd_value": ton_usd,
            "always_show": True,
        })

        def _coin_usd(sym: str, amount: Decimal, usd_value: Decimal | None) -> Decimal | None:
            """Кэшируем unit-price на удачных тиках и переиспользуем при пустых."""
            sym_key = (sym or "").upper()
            if usd_value is not None and amount > 0:
                try:
                    _last_unit_price_usd[sym_key] = usd_value / amount
                except Exception:
                    pass
                return usd_value
            if amount == 0:
                return Decimal("0")
            cached = _last_unit_price_usd.get(sym_key)
            if cached is not None:
                return amount * cached
            return None

        usdt_entry = next((j for j in jets if (j["symbol"] or "").upper() in {"USDT", "USD₮"}), None)
        if usdt_entry:
            coins.append({
                "symbol": "USDT",
                "amount": usdt_entry["amount"],
                "usd_value": _coin_usd("USDT", usdt_entry["amount"], usdt_entry["usd_value"]),
                "always_show": True,
            })
        else:
            coins.append({
                "symbol": "USDT",
                "amount": Decimal("0"),
                "usd_value": Decimal("0"),
                "always_show": True,
            })

        for jet in jets:
            sym = (jet["symbol"] or "").upper()
            if sym in {"USDT", "USD₮", "TON"}:
                continue
            if jet["amount"] <= 0:
                continue
            coins.append({
                "symbol": jet["symbol"],
                "amount": jet["amount"],
                "usd_value": _coin_usd(jet["symbol"], jet["amount"], jet["usd_value"]),
                "always_show": False,
            })

    # Apply display-mode filter.
    if display_mode == "min1usd":
        coins = [
            c for c in coins
            if c["usd_value"] is not None and c["usd_value"] >= Decimal("1")
        ]

    # Pagination state.
    total_pages = max(1, (len(coins) + COINS_PER_PAGE - 1) // COINS_PER_PAGE)
    if coin_page is None:
        coin_page = _current_coin_page.get(panel_user_id, 1)
    coin_page = max(1, min(coin_page, total_pages))
    _current_coin_page[panel_user_id] = coin_page

    page_start = (coin_page - 1) * COINS_PER_PAGE
    page_coins = coins[page_start:page_start + COINS_PER_PAGE]

    # Build text.
    lines: list[str] = []
    if accounts:
        acc = accounts[wallet_idx]
        name = _wallet_display_name(acc, lang)
        short_addr = _shorten(acc.external_ref)
        lines.append(f"<b>👛 {html.escape(name)} ({html.escape(short_addr)})</b>")

        total_usd = sum(
            (c["usd_value"] for c in coins if c["usd_value"] is not None), Decimal("0")
        )
        total_in_base, base_label = _convert_usd_to_base(
            total_usd, base_ccy, base_per_usd,
        )
        lines.append(f"<b>= {total_in_base:.2f} {base_label}</b>")
        lines.append("")

        for i, c in enumerate(page_coins):
            if i > 0:
                lines.append("")
            sym = c["symbol"] or "?"
            sym_escaped = html.escape(sym)
            link = COIN_LINKS.get(sym.upper())
            bold_name = f"<b>{sym_escaped}</b>"
            name_part = f'<a href="{link}">{bold_name}</a>' if link else bold_name
            amt_str = _format_coin_amount(c["amount"])
            line = f"{_coin_emoji(sym)} {name_part}: {amt_str} {sym_escaped}"
            if c["usd_value"] is not None:
                in_base, lbl = _convert_usd_to_base(
                    c["usd_value"], base_ccy, base_per_usd,
                )
                line += f" ({in_base:.2f} {_ccy_short(lbl)})"
            lines.append(line)
    else:
        lines.append(t("profile.crypto.empty", lang))

    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=panel_user_id,
        text="\n".join(lines),
        reply_markup=crypto_main_keyboard(
            wallets=wallets,
            current_idx=wallet_idx,
            lang=lang,
            coin_page=coin_page,
            coin_total_pages=total_pages,
            is_favorite=bool(accounts[wallet_idx].is_favorite) if accounts else False,
            current_address=accounts[wallet_idx].external_ref if accounts else None,
        ),
        parse_mode="HTML",
        disable_web_preview=True,
        force_new=force_new,
    )


async def _fetch_wallet_coins(acc) -> tuple[list[dict], Decimal | None]:
    """
    Возвращает (coins, ton_price_usd). coins — список dict с полями:
      symbol, amount (Decimal), usd_value (Decimal|None), unit_usd (Decimal|None)
    Используется и в send-flow, и в render_crypto_main.
    """
    try:
        ton_bal, jets, ton_price = await asyncio.gather(
            ton_service.get_live_balance_ton(acc),
            ton_service.get_jettons_detailed(acc),
            ton_service.ton_price_usd(),
        )
    except Exception:
        ton_bal, jets, ton_price = None, [], None

    ton_amount = ton_bal if ton_bal is not None else Decimal("0")
    coins: list[dict] = [{
        "symbol": "TON",
        "amount": ton_amount,
        "usd_value": (ton_amount * ton_price) if ton_price else None,
        "unit_usd": ton_price,
    }]
    for jet in jets:
        sym = (jet["symbol"] or "").upper().replace("₮", "T")
        unit = None
        if jet["amount"] and jet["amount"] > 0 and jet["usd_value"] is not None:
            try:
                unit = jet["usd_value"] / jet["amount"]
            except Exception:
                unit = None
        coins.append({
            "symbol": sym or "?",
            "amount": jet["amount"],
            "usd_value": jet["usd_value"],
            "unit_usd": unit,
        })
    return coins, ton_price


def _coin_min_amount(symbol: str, unit_usd: Decimal | None, ton_price: Decimal | None) -> Decimal:
    """Минимум для отправки конкретной монеты. Считается из SEND_MIN_TON по курсу."""
    if symbol.upper() == "TON":
        return SEND_MIN_TON
    if not ton_price or not unit_usd or unit_usd <= 0:
        return SEND_MIN_TON  # fallback
    return (SEND_MIN_TON * ton_price / unit_usd).quantize(Decimal("0.0000001"))


def _coin_fee(symbol: str, unit_usd: Decimal | None, ton_price: Decimal | None) -> tuple[Decimal, str]:
    """
    (fee_amount, fee_symbol). Фикс $0.2 в эквиваленте — конвертим в саму
    монету по её цене. Если цены нет — отдаём ноль (но это редко, т.к.
    мы фильтруем монеты по балансу заранее).
    """
    sym = symbol.upper()
    if sym == "TON":
        unit = ton_price
    else:
        unit = unit_usd
    if not unit or unit <= 0:
        return Decimal("0"), sym
    fee = (SEND_FEE_USD / unit).quantize(Decimal("0.00000001"))
    return fee, sym


async def _render_send_pick_coin(
    *, bot, chat_id: int, uid: int, uname: str | None,
) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"
        accounts = await ledger.get_active_accounts_by_type(
            db, user.id, AccountType.TON_WALLET,
        )
    if not accounts:
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return
    idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
    acc = accounts[idx]
    coins, ton_price = await _fetch_wallet_coins(acc)

    # Фильтр: только то, что покрывает минимум + комиссию.
    available: list[dict] = []
    for c in coins:
        if not c["amount"] or c["amount"] <= 0:
            continue
        min_amt = _coin_min_amount(c["symbol"], c["unit_usd"], ton_price)
        fee_amt, fee_sym = _coin_fee(c["symbol"], c["unit_usd"], ton_price)
        # Чтобы вывести жетон — нужен ещё TON-газ. Не отсекаем тут (это проверим
        # при выборе), но минимум по самому жетону должен влезать.
        if c["amount"] >= min_amt:
            available.append({
                **c,
                "min_amt": min_amt,
                "fee_amt": fee_amt,
                "fee_sym": fee_sym,
            })
    available.sort(key=lambda x: (x["usd_value"] or Decimal("0")), reverse=True)

    body_lines: list[str] = [f"<b>{t('crypto.send.title', lang)}</b>", ""]
    for c in available:
        sym = c["symbol"]
        amt_str = _format_coin_amount(c["amount"])
        link = COIN_LINKS.get(sym.upper())
        name = f'<a href="{link}"><b>{html.escape(sym)}</b></a>' if link else f"<b>{html.escape(sym)}</b>"
        base_extra = ""
        if c["usd_value"] is not None:
            # Конвертация в базовую валюту юзера сделана в основной панели,
            # тут используем простой fallback в UAH через USD.
            pass
        body_lines.append(f"{_coin_emoji(sym)} {name}: {amt_str} {html.escape(sym)}")
    body_lines.append("")
    body_lines.append(
        t("crypto.send.min_line", lang).format(amt=_format_coin_amount(SEND_MIN_TON))
    )
    body_lines.append("")
    body_lines.append(f"<b>{t('crypto.send.pick_coin_body', lang)}</b>")

    if not available:
        body_lines = [
            f"<b>{t('crypto.send.title', lang)}</b>",
            "",
            t("crypto.send.no_coins", lang),
        ]

    coin_buttons: list[tuple[str, str]] = [
        (c["symbol"], f"{_coin_emoji(c['symbol'])} {c['symbol']}")
        for c in available
    ]
    per_page = 9
    total_pages = max(1, (len(coin_buttons) + per_page - 1) // per_page)
    page = _send_coin_page.get(uid, 1)
    page = max(1, min(page, total_pages))
    _send_coin_page[uid] = page

    # Сохраняем «прайс-лист» в state чтобы потом на следующих шагах не
    # пересчитывать.
    _send_state[uid] = {
        **(_send_state.get(uid, {})),
        "wallet_id": acc.id,
        "coins": {c["symbol"]: c for c in available},
        "ton_price": ton_price,
    }

    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text="\n".join(body_lines),
        reply_markup=crypto_send_coins_keyboard(
            coin_buttons, page=page, total_pages=total_pages, lang=lang,
        ),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_send_amount(
    *, bot, chat_id: int, uid: int, uname: str | None,
    insufficient: bool = False,
) -> None:
    state = _send_state.get(uid) or {}
    sym = state.get("symbol")
    if not sym:
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return
    coin = (state.get("coins") or {}).get(sym)
    if not coin:
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"

    bal_str = _format_coin_amount(coin["amount"])
    min_str = _format_coin_amount(coin["min_amt"])
    fee_str = _format_coin_amount(coin["fee_amt"])
    emoji = _coin_emoji(sym)
    fee_emoji = _coin_emoji(coin["fee_sym"])

    lines = [
        f"<b>{t('crypto.send.title', lang)}</b>",
        "",
        t("crypto.send.pick_amount_body_line_network", lang),
        "",
        t("crypto.send.pick_amount_body_line_balance", lang).format(
            emoji=emoji, amt=bal_str, sym=sym,
        ),
        "",
        t("crypto.send.pick_amount_body_line_min", lang).format(
            emoji=emoji, amt=min_str, sym=sym,
        ),
        "",
        t("crypto.send.pick_amount_body_line_fee", lang).format(
            emoji=fee_emoji, amt=fee_str, sym=coin["fee_sym"],
        ),
        "",
    ]
    # Считаем "максимум для отправки" с учётом комиссии (если та в той же монете).
    if coin["fee_sym"] == sym:
        max_amt = max(Decimal("0"), coin["amount"] - coin["fee_amt"])
    else:
        max_amt = coin["amount"]
    max_str = _format_coin_amount(max_amt)
    enabled = max_amt >= coin["min_amt"]
    if insufficient or not enabled:
        lines.append(
            t("crypto.send.insufficient", lang).format(
                emoji=emoji, sym=sym,
                min_amt=_format_coin_amount(coin["min_amt"] + (coin["fee_amt"] if coin["fee_sym"] == sym else Decimal("0"))),
            )
        )
    else:
        lines.append(t("crypto.send.enter_amount", lang))

    _pending_send_amount.add(uid)
    _pending_send_addr.discard(uid)
    _pending_send_memo.discard(uid)

    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text="\n".join(lines),
        reply_markup=crypto_send_amount_keyboard(
            max_amount=max_str, symbol=sym, lang=lang, enabled=enabled,
        ),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_send_addr(
    *, bot, chat_id: int, uid: int, uname: str | None,
) -> None:
    state = _send_state.get(uid) or {}
    sym = state.get("symbol")
    amount = state.get("amount")
    coin = (state.get("coins") or {}).get(sym) if sym else None
    if not sym or amount is None or not coin:
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"
    amt_str = _format_coin_amount(amount)
    fee_str = _format_coin_amount(coin["fee_amt"])
    emoji = _coin_emoji(sym)
    fee_emoji = _coin_emoji(coin["fee_sym"])
    lines = [
        f"<b>{t('crypto.send.title', lang)}</b>",
        "",
        t("crypto.send.pick_amount_body_line_network", lang),
        "",
        t("crypto.send.amount_line", lang).format(emoji=emoji, amt=amt_str, sym=sym),
        t("crypto.send.pick_amount_body_line_fee", lang).format(
            emoji=fee_emoji, amt=fee_str, sym=coin["fee_sym"],
        ),
        "",
        t("crypto.send.enter_addr", lang),
    ]
    _pending_send_amount.discard(uid)
    _pending_send_addr.add(uid)
    _pending_send_memo.discard(uid)
    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text="\n".join(lines),
        reply_markup=crypto_send_addr_keyboard(lang=lang),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_send_confirm(
    *, bot, chat_id: int, uid: int, uname: str | None,
) -> None:
    state = _send_state.get(uid) or {}
    sym = state.get("symbol")
    amount = state.get("amount")
    address = state.get("address")
    coin = (state.get("coins") or {}).get(sym) if sym else None
    if not all([sym, amount, address, coin]):
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"

    amt_str = _format_coin_amount(amount)
    fee_str = _format_coin_amount(coin["fee_amt"])
    emoji = _coin_emoji(sym)
    fee_emoji = _coin_emoji(coin["fee_sym"])
    short_addr = _shorten(address)
    tonviewer = f"https://tonviewer.com/{address}"
    lines = [
        f"<b>{t('crypto.send.title', lang)}</b>",
        "",
        t("crypto.send.pick_amount_body_line_network", lang),
        "",
        t("crypto.send.amount_line", lang).format(emoji=emoji, amt=amt_str, sym=sym),
        t("crypto.send.pick_amount_body_line_fee", lang).format(
            emoji=fee_emoji, amt=fee_str, sym=coin["fee_sym"],
        ),
        "",
        t("crypto.send.addr_line", lang).format(
            url=tonviewer, short=html.escape(short_addr),
        ),
    ]
    memo = state.get("memo")
    if memo:
        lines.append("")
        lines.append(t("crypto.send.memo_line", lang).format(memo=html.escape(memo)))
    lines.append("")
    lines.append(t("crypto.send.confirm_title", lang))

    _pending_send_amount.discard(uid)
    _pending_send_addr.discard(uid)
    _pending_send_memo.discard(uid)

    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text="\n".join(lines),
        reply_markup=crypto_send_confirm_keyboard(lang=lang),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_send_memo(
    *, bot, chat_id: int, uid: int, uname: str | None,
) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"
    _pending_send_amount.discard(uid)
    _pending_send_addr.discard(uid)
    _pending_send_memo.add(uid)
    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text=f"<b>{t('crypto.send.memo_title', lang)}</b>\n\n{t('crypto.send.memo_body', lang)}",
        reply_markup=crypto_send_memo_keyboard(lang=lang),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_send_processing(
    *, bot, chat_id: int, uid: int, uname: str | None,
) -> None:
    state = _send_state.get(uid) or {}
    sym = state.get("symbol")
    amount = state.get("amount")
    address = state.get("address")
    if not all([sym, amount, address]):
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"
    short_addr = _shorten(address)
    tonviewer = f"https://tonviewer.com/{address}"
    text = (
        f"<b>{t('crypto.send.title_progress', lang)}</b>\n\n"
        + t("crypto.send.processing_body_html", lang).format(
            emoji=_coin_emoji(sym), amt=_format_coin_amount(amount),
            sym=sym, url=tonviewer, short=html.escape(short_addr),
        )
    )
    await push_text_panel(
        bot=bot, chat_id=chat_id, user_id=uid,
        text=text,
        reply_markup=crypto_send_processing_keyboard(lang=lang),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _execute_send(
    *, bot, uid: int, uname: str | None,
) -> None:
    """
    Реальная отправка средств. Сейчас — заглушка: ждём 5 секунд и шлём
    в чат уведомление об успешном завершении с фейковой ссылкой. Логика
    подписи через сохранённый seed + tonutils — TODO следующего коммита.
    """
    import logging as _lg
    log_ = _lg.getLogger(__name__)
    state = _send_state.get(uid) or {}
    sym = state.get("symbol")
    amount = state.get("amount")
    address = state.get("address")
    log_.info(
        "send execute start uid=%s sym=%s amount=%s address=%s",
        uid, sym, amount, address,
    )
    if not all([sym, amount, address]):
        log_.warning("send execute: missing state, aborting")
        return
    memo = state.get("memo")
    wallet_id = state.get("wallet_id")
    try:
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            account = await ledger.get_account_by_id(db, user.id, wallet_id) if wallet_id else None
        if not account or not account.encrypted_secret:
            raise RuntimeError("Кошелёк без сохранённого seed — отправка невозможна.")
        seed_phrase = _cipher.decrypt(account.encrypted_secret)

        # Комиссия в той же валюте — берём из ранее посчитанного _coin_fee.
        coin = (state.get("coins") or {}).get(sym) or {}
        fee_amount = coin.get("fee_amt") or Decimal("0")
        if coin.get("fee_sym") != sym:
            fee_amount = Decimal("0")  # batch требует одну валюту
        log_.info(
            "send fee plan: sym=%s amount=%s fee_amt=%s fee_sym=%s",
            sym, amount, fee_amount, coin.get("fee_sym"),
        )

        from app.services.send_service import execute_transfer, SendError
        try:
            tx_hash = await execute_transfer(
                seed_phrase=seed_phrase,
                from_address=account.external_ref,
                to_address=address,
                symbol=sym,
                amount=amount,
                memo=memo,
                fee_amount=fee_amount,
            )
        except SendError as exc:
            log_.warning("send refused: %s", exc)
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=f"❌ {html.escape(str(exc))}",
                    parse_mode="HTML",
                )
            except Exception:
                pass
            return

        tonviewer = (
            f"https://tonviewer.com/transaction/{tx_hash}"
            if tx_hash else f"https://tonviewer.com/{account.external_ref}"
        )
        text = t("crypto.send.done_notify_html", lang).format(
            url=tonviewer, amt=_format_coin_amount(amount), sym=sym, base="",
        )
        await bot.send_message(
            chat_id=uid, text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
        log_.info("send execute done uid=%s tx=%s", uid, tx_hash)
    except Exception as exc:
        log_.exception("send execute failed: %s", exc)
        try:
            await bot.send_message(
                chat_id=uid,
                text=f"❌ {html.escape(str(exc))}",
                parse_mode="HTML",
            )
        except Exception:
            pass
    finally:
        _send_state.pop(uid, None)


async def _render_crypto_reorder(
    *,
    bot: Bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
) -> None:
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        lang = getattr(user, "language", "ru") or "ru"
        accounts = await ledger.get_active_accounts_by_type(
            db, user.id, AccountType.TON_WALLET,
        )
    wallets: list[tuple[int, str]] = [
        (acc.id, _shorten(acc.external_ref)) for acc in accounts
    ]
    selected_id = _pending_reorder_pick.get(panel_user_id)
    text = (
        f"<b>{t('crypto.reorder.title', lang)}</b>\n\n"
        f"{t('crypto.reorder.subtitle', lang)}"
    )
    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=panel_user_id,
        text=text,
        reply_markup=crypto_reorder_keyboard(
            wallets, lang=lang, selected_account_id=selected_id,
        ),
        parse_mode="HTML",
        disable_web_preview=True,
    )


async def _render_crypto_wallet_view(
    *,
    bot: Bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
) -> None:
    await render_crypto_main(
        bot=bot,
        chat_id=chat_id,
        panel_user_id=panel_user_id,
        telegram_id=telegram_id,
        username=username,
    )

def _format_pct(current: Decimal, past: Decimal | None) -> str:
    """
    Returns ' (+5.20%)' / ' (-3.10%)' / '' depending on past availability.
    Empty string when no past data exists or past is zero (avoid div-by-zero).
    """
    if past is None or past <= 0 or current is None:
        return ""
    diff = current - past
    pct = (diff / past) * Decimal("100")
    sign = "+" if pct >= 0 else ""
    return f" ({sign}{pct:.2f}%)"


async def _save_profile_snapshot_if_due(
    db,
    user_id: int,
    *,
    total_usd: Decimal,
    total_uah: Decimal,
    mono_usd: Decimal,
    ton_usd: Decimal,
    gifts_usd: Decimal,
) -> None:
    """Insert a daily profile snapshot if today's hasn't been recorded yet."""
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    existing = (
        await db.execute(
            select(ProfileSnapshot.id)
            .where(
                and_(
                    ProfileSnapshot.user_id == user_id,
                    ProfileSnapshot.snapshot_at >= today_start,
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        ProfileSnapshot(
            user_id=user_id,
            snapshot_at=datetime.utcnow(),
            total_usd=total_usd,
            total_uah=total_uah,
            mono_usd=mono_usd,
            ton_usd=ton_usd,
            gifts_usd=gifts_usd,
        )
    )
    try:
        await db.commit()
    except Exception:
        pass


async def _historic_profile_snapshot(
    db, user_id: int, period: str
) -> ProfileSnapshot | None:
    """
    Returns the snapshot closest to (now − period_days). Looks for any snapshot
    in the window [target − tolerance, target + tolerance]; tolerance scales with
    the period so weekly views match within ±2 days, yearly within ±15.
    """
    days = _PERIOD_DAYS.get(period, 7)
    target = datetime.utcnow() - timedelta(days=days)
    tolerance = max(2, days // 10)
    earliest = target - timedelta(days=tolerance)
    latest = target + timedelta(days=tolerance)
    return (
        await db.execute(
            select(ProfileSnapshot)
            .where(
                and_(
                    ProfileSnapshot.user_id == user_id,
                    ProfileSnapshot.snapshot_at >= earliest,
                    ProfileSnapshot.snapshot_at <= latest,
                )
            )
            .order_by(ProfileSnapshot.snapshot_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _gift_emoji(slug: str) -> str:
    for prefix, emoji in _gift_emoji_map.items():
        if slug.lower().startswith(prefix.lower()):
            return emoji
    return "🎁"


def _profile_caption(
    *,
    total_uah: Decimal,
    total_usd: Decimal,
    mono_total_usd: Decimal,
    ton_total_usd: Decimal,
    mono_lines: list[tuple[str, Decimal, str]],   # (label, balance, currency_code)
    ton_lines: list[tuple[str, Decimal]],          # (label, total_usd)
    gift_balance_usd: Decimal,
    basic_gifts_usd: Decimal,
    gift_items: list[tuple[str, Decimal]],
    delta_total: str = "",
    delta_mono: str = "",
    delta_ton: str = "",
    delta_gifts: str = "",
    lang: str = "ru",
) -> str:
    from app.i18n import t as _t

    lines: list[str] = []
    # Header — drop the bare "Total balance" word, keep emoji + numbers + delta
    # so the line stays compact across languages.
    lines.append(f"<b>🏆 {total_uah:.2f} UAH / {total_usd:.2f} USD{delta_total}</b>")

    if mono_lines:
        lines.append("")
        mono_title = _t("profile.monobank", lang).replace("🟢 ", "")
        lines.append(f"<b>🟢 {mono_title}: {mono_total_usd:.2f} USD{delta_mono}</b>")
        body = [
            f"{html.escape(label)}: {balance:.2f} {currency}"
            for label, balance, currency in mono_lines
        ]
        lines.append(f"<blockquote expandable>{chr(10).join(body)}</blockquote>")

    if ton_lines:
        lines.append("")
        crypto_title = _t("profile.crypto", lang).replace("💎 ", "")
        lines.append(f"<b>💎 {crypto_title}: {ton_total_usd:.2f} USD{delta_ton}</b>")
        body = [
            f"TON ({html.escape(label)}): {usd:.2f} USD"
            for label, usd in ton_lines
        ]
        lines.append(f"<blockquote expandable>{chr(10).join(body)}</blockquote>")

    gifts_label = _t("profile.gifts", lang).replace("🎁 ", "")
    unimproved_label = _t("profile.gifts_basic", lang)
    lines.append("")
    lines.append(f"<b>🎁 {gifts_label}: {gift_balance_usd:.2f} USD{delta_gifts}</b>")
    if gift_items:
        gift_lines = [
            f'<a href="https://t.me/nft/{html.escape(slug)}">{html.escape(slug)}</a>: {ton_val:.2f} TON'
            for slug, ton_val in gift_items
        ]
        gift_lines.append(f"{unimproved_label}: {basic_gifts_usd:.2f} USD")
        lines.append(f"<blockquote expandable>{chr(10).join(gift_lines)}</blockquote>")
    elif basic_gifts_usd > 0:
        lines.append(
            f"<blockquote expandable>{unimproved_label}: {basic_gifts_usd:.2f} USD</blockquote>"
        )

    return "\n".join(lines)


async def _load_basic_gifts(db, user_id: int) -> list[BasicGiftItem]:
    rows = (
        await db.execute(
            select(BasicGiftItem).where(BasicGiftItem.user_id == user_id).order_by(BasicGiftItem.id)
        )
    ).scalars().all()
    return list(rows)


async def _ensure_default_basic_gifts(db, user_id: int) -> list[BasicGiftItem]:
    rows = await _load_basic_gifts(db, user_id)
    if rows:
        return rows
    now = datetime.utcnow()
    for gift_name, gift_id, price_usd in _DEFAULT_BASIC_GIFTS:
        db.add(
            BasicGiftItem(
                user_id=user_id,
                gift_id=gift_id,
                gift_name=gift_name,
                price_usd=price_usd,
                updated_at=now,
            )
        )
    await db.commit()
    return await _load_basic_gifts(db, user_id)


def _parse_basic_gift_line(line: str) -> tuple[str, str, str, Decimal] | None:
    """
    Returns:
      ("upsert", gift_id, gift_name, price_usd) for 'Name, id, price'
      ("delete", gift_id, "", 0) for 'del, id' or 'delete, id'
    """
    raw = line.strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) >= 2 and parts[0].lower() in {"del", "delete", "rm", "remove"}:
        gift_id = parts[1]
        if not gift_id:
            return None
        return ("delete", gift_id, "", Decimal("0"))
    if len(parts) < 3:
        return None
    gift_name = parts[0]
    gift_id = parts[1]
    price_raw = parts[2].replace(" ", "")
    if not gift_name or not gift_id or not price_raw:
        return None
    try:
        price_usd = Decimal(price_raw)
    except Exception:
        return None
    return ("upsert", gift_id, gift_name, price_usd)


def _format_custom_profile_text(raw: str) -> str:
    lines = []
    quote_pattern = re.compile(r'"([^"]+)"')
    for src_line in raw.splitlines():
        line = src_line.strip()
        if not line:
            lines.append("")
            continue
        if line.endswith(":"):
            lines.append(html.escape(line))
            continue

        parts = []
        last = 0
        for match in quote_pattern.finditer(line):
            start, end = match.span()
            outside = line[last:start].strip()
            if outside:
                parts.append(f"<b>{html.escape(outside)}</b>")
            quoted = match.group(1).strip()
            if quoted:
                parts.append(f"<blockquote expandable>{html.escape(quoted)}</blockquote>")
            last = end
        tail = line[last:].strip()
        if tail:
            parts.append(f"<b>{html.escape(tail)}</b>")
        if parts:
            lines.append("\n".join(parts))
        else:
            lines.append(f"<b>{html.escape(line)}</b>")
    return "\n".join(lines)


async def render_profile(
    *,
    bot: Bot,
    chat_id: int,
    panel_user_id: int,
    telegram_id: int,
    username: str | None,
    period: str,
) -> None:
    if period not in {"week", "month", "year"}:
        period = "week"
    now = time()
    async with SessionLocal() as db:
        try:
            user = await ledger.ensure_user(db, telegram_id, username)
        except Exception:
            from app.i18n import t as _t
            await bot.send_message(chat_id, _t("error_load_profile", "ru"))
            return

        cached = _profile_snapshot_cache.get(user.id)
        if cached and (now - cached["at"]) < BALANCE_CACHE_TTL_SECONDS:
            total_uah = cached["total_uah"]
            total_usd = cached["total_usd"]
            mono_total_usd = cached["mono_total_usd"]
            ton_total_usd = cached["ton_total_usd"]
            mono_lines = cached["mono_lines"]
            ton_lines = cached["ton_lines"]
            gift_usd = cached["gift_usd"]
            basic_gifts_usd = cached.get("basic_gifts_usd", Decimal("0"))
        else:
            # Profile total = sum of all linked card/wallet balances + gift value.
            try:
                rate = await fx_service.latest_uah_per_usd(db)
                mono_accounts = await ledger.get_active_accounts_by_type(
                    db, user.id, AccountType.MONOBANK_CARD
                )
                ton_accounts = await ledger.get_active_accounts_by_type(
                    db, user.id, AccountType.TON_WALLET
                )

                async def _fetch_mono_one(acc):
                    """Returns (account, (balance, currency, pan_label))."""
                    res = await monobank_service.get_live_balance(acc)
                    return acc, res

                async def _fetch_ton_one(acc):
                    """Returns (account, balance_in_ton, jettons_usd)."""
                    bal_task = ton_service.get_live_balance_ton(acc)
                    jet_task = ton_service.get_jetton_balances_usd(acc)
                    bal, (_, jet_usd) = await asyncio.gather(bal_task, jet_task)
                    return acc, bal, jet_usd

                async def _resolve_gifts():
                    # Hydrate in-memory cache from DB on cold start (e.g. after restart).
                    if user.id not in _gift_items_cache:
                        try:
                            persisted = await gift_service.load_persisted_items(db, user.id)
                        except Exception:
                            persisted = []
                        if persisted:
                            _gift_items_cache[user.id] = (persisted, 0.0)  # stale-by-design
                            _gift_slugs_cache[user.id] = [s for s, _ in persisted]

                    cached_items_entry = _gift_items_cache.get(user.id)
                    if cached_items_entry and (now - cached_items_entry[1]) < GIFT_PRICE_TTL_SECONDS:
                        return cached_items_entry[0]
                    # Stale cache is OK — show last known prices instantly, refresh in
                    # background. Profile is never rendered with empty/zero data once
                    # we've ever synced.
                    if cached_items_entry:
                        _schedule_price_refresh(
                            bot=bot, user_id=user.id, telegram_id=telegram_id,
                            chat_id=chat_id, panel_user_id=panel_user_id,
                            username=username, period=period,
                        )
                        return cached_items_entry[0]

                    cached_slugs = _gift_slugs_cache.get(user.id)
                    if cached_slugs is None:
                        # Truly first-ever visit: scrape slugs AND compute prices in bg.
                        _schedule_full_gift_sync(
                            bot=bot, user_id=user.id, telegram_id=telegram_id,
                            chat_id=chat_id, panel_user_id=panel_user_id,
                            username=username, period=period,
                        )
                        return []
                    if not cached_slugs:
                        _gift_items_cache[user.id] = ([], now)
                        return []
                    _, items = await gift_service.calculate_external_gifts_value(cached_slugs)
                    _gift_items_cache[user.id] = (items, now)
                    return items

                # Run everything in parallel.
                mono_results, ton_results, gift_items, ton_price, regular_gift_counts, unique_slug_by_gift_id = await asyncio.gather(
                    asyncio.gather(*[_fetch_mono_one(a) for a in mono_accounts]),
                    asyncio.gather(*[_fetch_ton_one(a) for a in ton_accounts]),
                    _resolve_gifts(),
                    ton_service.ton_price_usd(),
                    gift_service.scraper_service.get_regular_gift_counts_for_user(bot, telegram_id),
                    gift_service.scraper_service.get_unique_slug_by_regular_gift_id_for_user(bot, telegram_id),
                )
                basic_gifts = await _ensure_default_basic_gifts(db, user.id)

                uah = Decimal("0")
                usd = Decimal("0")
                mono_uah = Decimal("0")
                mono_usd = Decimal("0")
                mono_lines: list[tuple[str, Decimal, str]] = []

                for acc, res in mono_results:
                    if not res:
                        # No live data — show last known display_name with 0 balance.
                        label = _mono_display_label(acc, None, getattr(user, "language", "ru") or "ru")
                        mono_lines.append((label, Decimal("0"), "UAH"))
                        continue
                    amount, currency, pan_label = res
                    if currency == Currency.UAH:
                        uah += amount
                        mono_uah += amount
                    elif currency == Currency.USD:
                        usd += amount
                        mono_usd += amount

                    # Refresh stale auto-label.
                    if pan_label and acc.display_name in {"Monobank card", "", None}:
                        acc.display_name = pan_label
                        try:
                            await db.commit()
                        except Exception:
                            pass

                    label = _mono_display_label(acc, pan_label, getattr(user, "language", "ru") or "ru")
                    mono_lines.append((label, amount, currency.value if hasattr(currency, "value") else str(currency)))

                ton_lines: list[tuple[str, Decimal]] = []
                ton_total_usd = Decimal("0")
                for acc, ton_bal, jet_usd in ton_results:
                    ton_native_usd = Decimal("0")
                    if ton_bal is not None and ton_price is not None:
                        ton_native_usd = ton_bal * ton_price
                    wallet_usd = ton_native_usd + (jet_usd or Decimal("0"))
                    usd += wallet_usd
                    ton_total_usd += wallet_usd
                    label = _ton_display_label(acc)
                    ton_lines.append((label, wallet_usd))

                gift_val = sum((p for _, p in gift_items), Decimal("0"))
                basic_gifts_usd = Decimal("0")
                for item in basic_gifts:
                    qty = regular_gift_counts.get(str(item.gift_id), 0)
                    if qty <= 0:
                        continue
                    basic_gifts_usd += Decimal(item.price_usd or 0) * Decimal(qty)

                # Extra value: regular gifts that share base gift_id with owned unique NFTs.
                # Price them by collection floor (TON -> USD) and add on top.
                floor_bonus_usd = Decimal("0")
                if ton_price:
                    floor_ton_by_gift_id: dict[str, Decimal] = {}
                    for gift_id, slug in unique_slug_by_gift_id.items():
                        floor_ton = await gift_service.get_collection_floor_by_slug(slug)
                        if floor_ton > 0:
                            floor_ton_by_gift_id[gift_id] = floor_ton
                    for gift_id, floor_ton in floor_ton_by_gift_id.items():
                        qty = regular_gift_counts.get(gift_id, 0)
                        if qty <= 0:
                            continue
                        floor_bonus_usd += floor_ton * ton_price * Decimal(qty)
                basic_gifts_usd += floor_bonus_usd
                gift_usd = Decimal("0")
                if gift_val > 0 and ton_price:
                    gift_usd = gift_val * ton_price
                gift_usd += basic_gifts_usd
                usd += gift_usd

                total_uah = uah + (usd * rate)
                total_usd = usd + (uah / rate if rate else Decimal("0"))
                mono_total_usd = mono_usd + (mono_uah / rate if rate else Decimal("0"))

                _profile_snapshot_cache[user.id] = {
                    "at": now,
                    "total_uah": total_uah,
                    "total_usd": total_usd,
                    "mono_total_usd": mono_total_usd,
                    "ton_total_usd": ton_total_usd,
                    "mono_lines": mono_lines,
                    "ton_lines": ton_lines,
                    "gift_usd": gift_usd,
                    "basic_gifts_usd": basic_gifts_usd,
                }
                # Persist daily aggregate so future profile views can show
                # period-over-period deltas.
                await _save_profile_snapshot_if_due(
                    db, user.id,
                    total_usd=total_usd,
                    total_uah=total_uah,
                    mono_usd=mono_total_usd,
                    ton_usd=ton_total_usd,
                    gifts_usd=gift_usd,
                )
            except Exception:
                from app.i18n import t as _t
                await bot.send_message(chat_id, _t("error_balance_data", "ru"))
                return

        # Compute period-over-period deltas vs the snapshot from N days ago.
        delta_total = delta_mono = delta_ton = delta_gifts = ""
        async with SessionLocal() as delta_db:
            past = await _historic_profile_snapshot(delta_db, user.id, period)
        if past:
            delta_total = _format_pct(total_usd, Decimal(past.total_usd or 0))
            delta_mono = _format_pct(mono_total_usd, Decimal(past.mono_usd or 0))
            delta_ton = _format_pct(ton_total_usd, Decimal(past.ton_usd or 0))
            delta_gifts = _format_pct(gift_usd, Decimal(past.gifts_usd or 0))

        gift_items_entry = _gift_items_cache.get(user.id)
        gift_items = gift_items_entry[0] if gift_items_entry else []
        user_lang = getattr(user, "language", "ru") or "ru"
        _profile_view_by_user[panel_user_id] = "analytics"
        caption = _profile_caption(
            total_uah=total_uah,
            total_usd=total_usd,
            mono_total_usd=mono_total_usd,
            ton_total_usd=ton_total_usd,
            mono_lines=mono_lines,
            ton_lines=ton_lines,
            gift_balance_usd=gift_usd,
            basic_gifts_usd=basic_gifts_usd,
            gift_items=gift_items,
            delta_total=delta_total,
            delta_mono=delta_mono,
            delta_ton=delta_ton,
            delta_gifts=delta_gifts,
            lang=user_lang,
        )

        custom_text = _custom_profile_text.get(user.id)
        if custom_text:
            await push_text_panel(
                bot=bot,
                chat_id=chat_id,
                user_id=panel_user_id,
                text=_format_custom_profile_text(custom_text),
                reply_markup=profile_period_keyboard(
                    selected_period=period, lang=user_lang, selected_view="analytics"
                ),
                parse_mode="HTML",
            )
            return

        chart_path = None
        try:
            user_ccy = user.base_currency.value if user.base_currency else "USD"
            chart_path = await report_service.generate_profile_chart(
                db, user.id, period=period, lang=user_lang, currency_code=user_ccy,
            )
        except Exception:
            chart_path = None

    if chart_path:
        await push_photo_panel(
            bot=bot,
            chat_id=chat_id,
            user_id=panel_user_id,
            photo_path=str(chart_path),
            caption=caption,
            reply_markup=profile_period_keyboard(
                selected_period=period, lang=user_lang, selected_view="analytics"
            ),
            parse_mode="HTML",
        )
        return

    await push_text_panel(
        bot=bot,
        chat_id=chat_id,
        user_id=panel_user_id,
        text=caption,
        reply_markup=profile_period_keyboard(
            selected_period=period, lang=user_lang, selected_view="analytics"
        ),
        parse_mode="HTML",
    )


async def build_profile_text_for_inline(
    *,
    bot: Bot,
    telegram_id: int,
    username: str | None,
    period: str = "week",
) -> str:
    """
    Builds profile text (without chart/photo) for inline mode.
    """
    if period not in {"week", "month", "year"}:
        period = "week"
    now = time()
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, telegram_id, username)
        cached = _profile_snapshot_cache.get(user.id)
        if cached and (now - cached["at"]) < BALANCE_CACHE_TTL_SECONDS:
            total_uah = cached["total_uah"]
            total_usd = cached["total_usd"]
            mono_total_usd = cached.get("mono_total_usd", Decimal("0"))
            ton_total_usd = cached.get("ton_total_usd", Decimal("0"))
            mono_lines = cached["mono_lines"]
            ton_lines = cached["ton_lines"]
            gift_usd = cached["gift_usd"]
            basic_gifts_usd = cached.get("basic_gifts_usd", Decimal("0"))
        else:
            rate = await fx_service.latest_uah_per_usd(db)
            mono_accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.MONOBANK_CARD)
            ton_accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)

            async def _fetch_mono_one(acc):
                return acc, await monobank_service.get_live_balance(acc)

            async def _fetch_ton_one(acc):
                bal_task = ton_service.get_live_balance_ton(acc)
                jet_task = ton_service.get_jetton_balances_usd(acc)
                bal, (_, jet_usd) = await asyncio.gather(bal_task, jet_task)
                return acc, bal, jet_usd

            async def _resolve_gifts():
                cached_items_entry = _gift_items_cache.get(user.id)
                if cached_items_entry and (now - cached_items_entry[1]) < GIFT_PRICE_TTL_SECONDS:
                    return cached_items_entry[0]
                cached_slugs = _gift_slugs_cache.get(user.id, [])
                if not cached_slugs:
                    return cached_items_entry[0] if cached_items_entry else []
                _, items = await gift_service.calculate_external_gifts_value(cached_slugs)
                _gift_items_cache[user.id] = (items, now)
                return items

            mono_results, ton_results, gift_items, ton_price, regular_gift_counts, unique_slug_by_gift_id = await asyncio.gather(
                asyncio.gather(*[_fetch_mono_one(a) for a in mono_accounts]),
                asyncio.gather(*[_fetch_ton_one(a) for a in ton_accounts]),
                _resolve_gifts(),
                ton_service.ton_price_usd(),
                gift_service.scraper_service.get_regular_gift_counts_for_user(bot, telegram_id),
                gift_service.scraper_service.get_unique_slug_by_regular_gift_id_for_user(bot, telegram_id),
            )
            basic_gifts = await _ensure_default_basic_gifts(db, user.id)

            uah = Decimal("0")
            usd = Decimal("0")
            mono_uah = Decimal("0")
            mono_usd = Decimal("0")
            mono_lines: list[tuple[str, Decimal, str]] = []
            for acc, res in mono_results:
                if not res:
                    mono_lines.append((_mono_display_label(acc, None, getattr(user, "language", "ru") or "ru"), Decimal("0"), "UAH"))
                    continue
                amount, currency, pan_label = res
                if currency == Currency.UAH:
                    uah += amount
                    mono_uah += amount
                elif currency == Currency.USD:
                    usd += amount
                    mono_usd += amount
                mono_lines.append((_mono_display_label(acc, pan_label, getattr(user, "language", "ru") or "ru"), amount, currency.value if hasattr(currency, "value") else str(currency)))

            ton_lines: list[tuple[str, Decimal]] = []
            ton_total_usd = Decimal("0")
            for acc, ton_bal, jet_usd in ton_results:
                ton_native_usd = Decimal("0")
                if ton_bal is not None and ton_price is not None:
                    ton_native_usd = ton_bal * ton_price
                wallet_usd = ton_native_usd + (jet_usd or Decimal("0"))
                usd += wallet_usd
                ton_total_usd += wallet_usd
                ton_lines.append((_ton_display_label(acc), wallet_usd))

            gift_val = sum((p for _, p in gift_items), Decimal("0"))
            basic_gifts_usd = Decimal("0")
            for item in basic_gifts:
                qty = regular_gift_counts.get(str(item.gift_id), 0)
                if qty > 0:
                    basic_gifts_usd += Decimal(item.price_usd or 0) * Decimal(qty)

            if ton_price:
                for gift_id, slug in unique_slug_by_gift_id.items():
                    qty = regular_gift_counts.get(gift_id, 0)
                    if qty <= 0:
                        continue
                    floor_ton = await gift_service.get_collection_floor_by_slug(slug)
                    if floor_ton > 0:
                        basic_gifts_usd += floor_ton * ton_price * Decimal(qty)

            gift_usd = (gift_val * ton_price) if (gift_val > 0 and ton_price) else Decimal("0")
            gift_usd += basic_gifts_usd
            usd += gift_usd

            total_uah = uah + (usd * rate)
            total_usd = usd + (uah / rate if rate else Decimal("0"))
            mono_total_usd = mono_usd + (mono_uah / rate if rate else Decimal("0"))
            _profile_snapshot_cache[user.id] = {
                "at": now,
                "total_uah": total_uah,
                "total_usd": total_usd,
                "mono_total_usd": mono_total_usd,
                "ton_total_usd": ton_total_usd,
                "mono_lines": mono_lines,
                "ton_lines": ton_lines,
                "gift_usd": gift_usd,
                "basic_gifts_usd": basic_gifts_usd,
            }
            await _save_profile_snapshot_if_due(
                db, user.id,
                total_usd=total_usd,
                total_uah=total_uah,
                mono_usd=mono_total_usd,
                ton_usd=ton_total_usd,
                gifts_usd=gift_usd,
            )

        # Compute period-over-period deltas.
        delta_total = delta_mono = delta_ton = delta_gifts = ""
        async with SessionLocal() as delta_db:
            past = await _historic_profile_snapshot(delta_db, user.id, period)
        if past:
            delta_total = _format_pct(total_usd, Decimal(past.total_usd or 0))
            delta_mono = _format_pct(mono_total_usd, Decimal(past.mono_usd or 0))
            delta_ton = _format_pct(ton_total_usd, Decimal(past.ton_usd or 0))
            delta_gifts = _format_pct(gift_usd, Decimal(past.gifts_usd or 0))

        gift_items_entry = _gift_items_cache.get(user.id)
        gift_items = gift_items_entry[0] if gift_items_entry else []
        caption = _profile_caption(
            total_uah=total_uah,
            total_usd=total_usd,
            mono_total_usd=mono_total_usd,
            ton_total_usd=ton_total_usd,
            mono_lines=mono_lines,
            ton_lines=ton_lines,
            gift_balance_usd=gift_usd,
            basic_gifts_usd=basic_gifts_usd,
            gift_items=gift_items,
            delta_total=delta_total,
            delta_mono=delta_mono,
            delta_ton=delta_ton,
            delta_gifts=delta_gifts,
            lang=getattr(user, "language", "ru") or "ru",
        )
        uname = f"@{username}" if username else f"id{telegram_id}"
        lang = getattr(user, "language", "ru") or "ru"
        return f"<b>{t('profile.title', lang, name=html.escape(uname))}</b>\n\n{caption}"


@router.message(Command("profile"))
async def profile_command(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    await render_crypto_main(
        bot=message.bot,
        chat_id=message.chat.id,
        panel_user_id=message.from_user.id,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
    )


@router.message(Command("profile_text_next"))
async def profile_text_next(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
    _pending_profile_text_update.add(message.from_user.id)
    await push_text_panel(
        bot=message.bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        text=t("profile.custom_text_prompt", lang),
        reply_markup=profile_period_keyboard(
            selected_period="week", lang=lang, selected_view="analytics"
        ),
        parse_mode=None,
    )


@router.message(lambda m: bool(m.from_user and m.from_user.id in _pending_profile_text_update and m.text))
async def profile_custom_text_input(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    if message.from_user.id not in _pending_profile_text_update:
        return

    _pending_profile_text_update.discard(message.from_user.id)
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        _custom_profile_text[user.id] = message.text
        _profile_snapshot_cache.pop(user.id, None)
        _gift_items_cache.pop(user.id, None)
        _gift_slugs_cache.pop(user.id, None)

    await render_profile(
        bot=message.bot,
        chat_id=message.chat.id,
        panel_user_id=message.from_user.id,
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        period="week",
    )


@router.callback_query(F.data.startswith("profile:"))
async def profile_period_selected(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return
    
    parts = callback.data.split(":", 1)
    action = parts[1]

    if action.startswith("view:"):
        selected = action.split(":", 1)[1]
        if selected == "crypto":
            try:
                await callback.answer()
            except TelegramBadRequest:
                pass
            await _render_crypto_wallet_view(
                bot=callback.bot,
                chat_id=callback.message.chat.id,
                panel_user_id=callback.from_user.id,
                telegram_id=callback.from_user.id,
                username=callback.from_user.username,
            )
            return
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_profile(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            panel_user_id=callback.from_user.id,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            period="week",
        )
        return

    if action == "crypto_import_seed":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, callback.from_user.id, callback.from_user.username)
            lang = getattr(user, "language", "ru") or "ru"
        _pending_crypto_seed_import.add(callback.from_user.id)
        _pending_crypto_create_addr.pop(callback.from_user.id, None)
        await callback.answer()
        await push_text_panel(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            user_id=callback.from_user.id,
            text=t("profile.crypto.import_prompt", lang),
            reply_markup=profile_crypto_keyboard(lang),
            parse_mode="HTML",
        )
        return

    if action == "crypto_create_wallet":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, callback.from_user.id, callback.from_user.username)
            lang = getattr(user, "language", "ru") or "ru"
        seed = _generate_seed_phrase(24)
        _pending_crypto_create_addr[callback.from_user.id] = seed
        _pending_crypto_seed_import.discard(callback.from_user.id)
        await callback.answer()
        await push_text_panel(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            user_id=callback.from_user.id,
            text=t("profile.crypto.create_text", lang, seed=seed),
            reply_markup=profile_crypto_keyboard(lang),
            parse_mode="HTML",
        )
        return

    if action == "crypto_refresh":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_crypto_wallet_view(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            panel_user_id=callback.from_user.id,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
        )
        return
    
    if action == "refresh":
        # "Обновить подарки" — re-scrape only the slug list. Prices stay as cached;
        # the background 30-min cycle handles re-pricing independently.
        try:
            from app.i18n import get_user_lang as _gl, t as _t
            async with SessionLocal() as _db:
                _lang = await _gl(_db, callback.from_user.id)
            await callback.answer(_t("profile.refresh_toast", _lang))
        except TelegramBadRequest:
            pass

        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, callback.from_user.id, callback.from_user.username)
            _profile_snapshot_cache.pop(user.id, None)
            try:
                slugs = await gift_service.scrape_gift_slugs(callback.bot, callback.from_user.id)
            except Exception:
                slugs = _gift_slugs_cache.get(user.id, [])

            _gift_slugs_cache[user.id] = slugs

            # Reuse existing prices for known slugs; new slugs get 0 (will be priced
            # by the next background refresh cycle).
            existing_entry = _gift_items_cache.get(user.id)
            existing_prices = dict(existing_entry[0]) if existing_entry else {}
            new_items = sorted(
                ((s, existing_prices.get(s, Decimal("0"))) for s in slugs),
                key=lambda p: p[1], reverse=True,
            )
            old_ts = existing_entry[1] if existing_entry else 0
            _gift_items_cache[user.id] = (new_items, old_ts)
            # Persist new slug set so a restart still shows them.
            try:
                await gift_service.persist_items(db, user.id, new_items)
            except Exception:
                pass
        period = "week"
    else:
        period = action
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass

    await render_profile(
        bot=callback.bot,
        chat_id=callback.message.chat.id,
        panel_user_id=callback.from_user.id,
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        period=period,
    )


# ---------------------------------------------------------------------------
# Crypto main screen callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("crypto:"))
async def crypto_callback(callback: CallbackQuery) -> None:
    if not callback.data or not callback.from_user or not callback.message or not callback.bot:
        return

    action = callback.data.split(":", 1)[1]
    uid = callback.from_user.id
    bot = callback.bot
    chat_id = callback.message.chat.id
    uname = callback.from_user.username

    if action == "noop":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        return

    if action.startswith("open_wallet:"):
        # Из уведомления о пополнении — переключаемся на нужный кошелёк
        # и шлём НОВОЕ сообщение (force_new=True), чтобы сообщение про
        # деп не пропало с заменой панели на кошелёк.
        try:
            acc_id = int(action.split(":", 1)[1])
        except ValueError:
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            accounts = await ledger.get_active_accounts_by_type(
                db, user.id, AccountType.TON_WALLET,
            )
        for i, acc in enumerate(accounts):
            if acc.id == acc_id:
                _current_wallet_idx[uid] = i
                break
        _current_coin_page[uid] = 1
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, force_new=True,
        )
        return

    if action == "fav":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(
                db, user.id, AccountType.TON_WALLET,
            )
            if not accounts:
                try:
                    await callback.answer()
                except TelegramBadRequest:
                    pass
                return
            idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
            target = accounts[idx]
            if target.is_favorite:
                try:
                    await callback.answer(t("crypto.fav.already", lang))
                except TelegramBadRequest:
                    pass
                return
            for acc in accounts:
                acc.is_favorite = (acc.id == target.id)
            await db.commit()
        try:
            await callback.answer(t("crypto.fav.set", lang))
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action == "reorder":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        _pending_reorder_pick.pop(uid, None)
        await _render_crypto_reorder(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action.startswith("reorder_addr:"):
        try:
            acc_id = int(action.split(":", 1)[1])
        except ValueError:
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        prev = _pending_reorder_pick.get(uid)
        if prev is not None and prev != acc_id:
            # Уже что-то выбрано → второй тап на адрес заменяет выбор, не свапает.
            pass
        _pending_reorder_pick[uid] = acc_id
        try:
            await callback.answer(t("crypto.reorder.pick_num", lang), show_alert=False)
        except TelegramBadRequest:
            pass
        await _render_crypto_reorder(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action.startswith("reorder_num:"):
        try:
            target_pos = int(action.split(":", 1)[1])
        except ValueError:
            return
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(
                db, user.id, AccountType.TON_WALLET,
            )
            selected_id = _pending_reorder_pick.get(uid)
            if selected_id is None:
                # Без выбранного адреса тап на номер ничего не делает — это
                # запрошенный UX: свап стартует только с адреса.
                try:
                    await callback.answer()
                except TelegramBadRequest:
                    pass
                return
            # Работаем с ВИДИМЫМ порядком (как отдаёт get_active_accounts_by_type).
            # Берём список, вынимаем выбранный кошелёк, вставляем в target_pos-1,
            # затем переписываем sort_order 1..N всем подряд. Это переживает
            # ситуацию, когда у части аккаунтов sort_order=0 после миграции.
            current = list(accounts)
            src = next((a for a in current if a.id == selected_id), None)
            if src is None:
                _pending_reorder_pick.pop(uid, None)
                return
            current.remove(src)
            target_pos = max(1, min(target_pos, len(current) + 1))
            current.insert(target_pos - 1, src)
            for new_pos, acc in enumerate(current, start=1):
                acc.sort_order = new_pos
            await db.commit()
            _pending_reorder_pick.pop(uid, None)
            # После переупорядочивания — обновляем индекс «текущего» кошелька,
            # чтобы основная панель не показала чужой при выходе из reorder.
            for i, acc in enumerate(current):
                if acc.id == src.id:
                    _current_wallet_idx[uid] = i
                    break
        try:
            await callback.answer(t("crypto.reorder.swapped", lang))
        except TelegramBadRequest:
            pass
        await _render_crypto_reorder(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action.startswith("page:"):
        try:
            page = int(action.split(":", 1)[1])
        except ValueError:
            page = 1
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, coin_page=page,
        )
        return

    if action == "prev":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        idx = max(0, _current_wallet_idx.get(uid, 0) - 1)
        _current_coin_page[uid] = 1
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, wallet_idx=idx, coin_page=1,
        )
        return

    if action == "next":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        idx = _current_wallet_idx.get(uid, 0) + 1
        _current_coin_page[uid] = 1
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, wallet_idx=idx, coin_page=1,
        )
        return

    if action == "history":
        from app.bot.handlers.history import _user_history_filters, render_history
        from app.services.history_service import HistoryFilters
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        if accounts:
            idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
            filters = HistoryFilters(account_id=accounts[idx].id)
            lock_source = True
        else:
            filters = HistoryFilters(account_type=AccountType.TON_WALLET)
            lock_source = True
        _user_history_filters[uid] = filters
        await render_history(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, filters=filters,
            back_to="crypto:refresh",
            lock_source=lock_source,
        )
        return

    if action == "settings":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            wallets_count = len(
                await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
            )
        mode = _balance_display_mode.get(uid, "all")
        mode_label = t(
            "crypto.display.min1usd_short" if mode == "min1usd" else "crypto.display.all_short",
            lang,
        )
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.settings_title', lang)}</b>\n\n"
            + t("crypto.display_status", lang, mode=mode_label)
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_settings_keyboard(lang, has_wallet=wallets_count > 0),
            parse_mode="HTML",
        )
        return

    if action == "display":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        mode = _balance_display_mode.get(uid, "all")
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=f"<b>{t('crypto.display_title', lang)}</b>\n\n{t('crypto.display_subtitle', lang)}",
            reply_markup=crypto_display_mode_keyboard(mode, lang=lang),
            parse_mode="HTML",
        )
        return

    if action.startswith("display_set:"):
        new_mode = action.split(":", 1)[1]
        if new_mode not in {"all", "min1usd"}:
            new_mode = "all"
        _balance_display_mode[uid] = new_mode
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=f"<b>{t('crypto.display_title', lang)}</b>\n\n{t('crypto.display_subtitle', lang)}",
            reply_markup=crypto_display_mode_keyboard(new_mode, lang=lang),
            parse_mode="HTML",
        )
        return

    if action == "rename":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        if not accounts:
            try:
                await callback.answer(t("crypto.no_wallet", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return
        idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
        acc = accounts[idx]
        _pending_crypto_rename[uid] = acc.id
        _pending_crypto_seed_import.discard(uid)
        _pending_crypto_create_addr.pop(uid, None)
        has_custom = _is_custom_user_name(acc.display_name)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("crypto.rename_prompt", lang),
            reply_markup=crypto_rename_keyboard(lang, has_custom=has_custom),
            parse_mode="HTML",
        )
        return

    if action == "rename_clear":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
            if accounts:
                idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
                acc = accounts[idx]
                acc.display_name = "TON wallet"
                await db.commit()
                _profile_snapshot_cache.pop(user.id, None)
        _pending_crypto_rename.pop(uid, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action == "unlink_ask":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        if not accounts:
            try:
                await callback.answer(t("crypto.no_wallet", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("crypto.unlink_confirm", lang),
            reply_markup=crypto_unlink_confirm_keyboard(lang),
            parse_mode="HTML",
        )
        return

    if action == "unlink_yes":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
            if not accounts:
                try:
                    await callback.answer(t("crypto.no_wallet", lang), show_alert=True)
                except TelegramBadRequest:
                    pass
                return
            idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
            acc = accounts[idx]
            acc.is_active = False
            await db.commit()
            _profile_snapshot_cache.pop(user.id, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        _current_wallet_idx[uid] = 0
        _current_coin_page[uid] = 1
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname, wallet_idx=0, coin_page=1,
        )
        return

    if action == "import_seed":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        _pending_crypto_seed_import.add(uid)
        _pending_crypto_create_addr.pop(uid, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("profile.crypto.import_prompt", lang),
            reply_markup=crypto_settings_keyboard(lang),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "create_wallet":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        seed = _generate_seed_phrase(24)
        _pending_crypto_create_addr[uid] = seed
        _pending_crypto_seed_import.discard(uid)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=t("profile.crypto.create_text", lang, seed=seed),
            reply_markup=crypto_settings_keyboard(lang),
            parse_mode="HTML",
        )
        return

    if action == "refresh":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    if action == "new_menu":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.new.title', lang)}</b>\n\n"
            + t("crypto.new.subtitle", lang)
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_new_wallet_menu_keyboard(lang),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "new_import":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            existing = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        # Back goes to the new-wallet menu if the user has any wallet, otherwise to empty state.
        back_target = "crypto:new_menu" if existing else "crypto:refresh"
        _pending_crypto_seed_only.add(uid)
        _pending_crypto_seed_import.discard(uid)
        _pending_crypto_addr_only.discard(uid)
        _pending_crypto_create_addr.pop(uid, None)
        _pending_crypto_rename.pop(uid, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.import.title', lang)}</b>\n\n"
            + t("crypto.import.subtitle", lang)
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_import_prompt_keyboard(lang, back_to=back_target),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "new_import_addr":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            existing = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        back_target = "crypto:new_menu" if existing else "crypto:refresh"
        _pending_crypto_addr_only.add(uid)
        _pending_crypto_seed_only.discard(uid)
        _pending_crypto_seed_import.discard(uid)
        _pending_crypto_create_addr.pop(uid, None)
        _pending_crypto_rename.pop(uid, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.import_addr.title', lang)}</b>\n\n"
            + t("crypto.import_addr.subtitle", lang)
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_import_prompt_keyboard(lang, back_to=back_target),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "new_create":
        import logging
        log = logging.getLogger(__name__)
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            user_internal_id = user.id

        # CPU-bound generation/derivation outside the DB session.
        seed = _generate_seed_phrase(24)
        address = _derive_ton_address(seed)
        if not address:
            log.error("new_create: derivation failed for fresh seed (words=%d)", len(seed.split()))
            try:
                await callback.answer(t("crypto.import.not_found", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return

        try:
            async with SessionLocal() as db:
                account = await ton_service.link_wallet(db, user_internal_id, address)
                account.encrypted_secret = _cipher.encrypt(seed)
                await db.commit()
            _profile_snapshot_cache.pop(user_internal_id, None)
        except Exception as exc:
            log.exception("new_create: failed to persist wallet: %s", exc)
            try:
                await callback.answer(t("crypto.import.not_found", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return

        _last_generated_seed[uid] = seed
        # Switch view to the newly created wallet.
        _current_wallet_idx[uid] = 0
        _current_coin_page[uid] = 1
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.create.success_title', lang)}</b>\n\n"
            f"{t('crypto.create.success_subtitle', lang)}\n"
            f"<code>{html.escape(seed)}</code>"
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_create_success_keyboard(lang, seed=seed),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "info":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
            accounts = await ledger.get_active_accounts_by_type(db, user.id, AccountType.TON_WALLET)
        if not accounts:
            try:
                await callback.answer(t("crypto.no_wallet", lang), show_alert=True)
            except TelegramBadRequest:
                pass
            return
        idx = max(0, min(_current_wallet_idx.get(uid, 0), len(accounts) - 1))
        acc = accounts[idx]
        seed_clear = ""
        try:
            if acc.encrypted_secret:
                seed_clear = _cipher.decrypt(acc.encrypted_secret) or ""
        except Exception:
            seed_clear = ""
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        # Telegram HTML doesn't allow <code> inside <tg-spoiler>; use spoiler only.
        seed_block = (
            f"<tg-spoiler>{html.escape(seed_clear)}</tg-spoiler>"
            if seed_clear
            else "—"
        )
        text = (
            f"<b>{t('crypto.info.title', lang)}</b>\n\n"
            f"{t('crypto.info.seed_label', lang)}\n"
            f"{seed_block}\n\n"
            f"<blockquote>{t('crypto.info.warning', lang)}</blockquote>"
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_info_with_copy_keyboard(lang, seed=seed_clear),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "deposit":
        await _render_deposit_screen(
            bot=bot, chat_id=chat_id, uid=uid, uname=uname,
            callback=callback, qr_shown=False,
        )
        return

    if action == "dep_show_qr":
        await _render_deposit_screen(
            bot=bot, chat_id=chat_id, uid=uid, uname=uname,
            callback=callback, qr_shown=True,
        )
        return

    if action == "dep_hide_qr":
        await _render_deposit_screen(
            bot=bot, chat_id=chat_id, uid=uid, uname=uname,
            callback=callback, qr_shown=False,
        )
        return

    if action == "dep_help":
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        text = (
            f"<b>{t('crypto.deposit.help_title', lang)}</b>\n\n"
            f"{t('crypto.deposit.help_body', lang)}"
        )
        await push_text_panel(
            bot=bot, chat_id=chat_id, user_id=uid,
            text=text,
            reply_markup=crypto_deposit_help_keyboard(lang),
            parse_mode="HTML",
            disable_web_preview=True,
        )
        return

    if action == "dep_check":
        # Сбрасываем кэш балансов и перерисовываем главный кошелёчный экран.
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        try:
            await callback.answer(t("crypto.deposit.check_done", lang))
        except TelegramBadRequest:
            pass
        _profile_snapshot_cache.pop(user.id, None)
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    # Кнопка "Связаться с поддержкой" теперь URL-кнопка → callback не приходит.

    if action == "withdraw" or action == "send_start":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        # При входе в flow сбрасываем выбор монеты и предыдущие шаги,
        # но сохраняем wallet_id (он привязан к текущему индексу).
        if action == "withdraw":
            _send_state.pop(uid, None)
        else:
            st = _send_state.get(uid, {})
            st.pop("symbol", None)
            st.pop("amount", None)
            st.pop("address", None)
            st.pop("memo", None)
            _send_state[uid] = st
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action.startswith("send_coin_page:"):
        try:
            _send_coin_page[uid] = int(action.split(":", 1)[1])
        except ValueError:
            return
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action.startswith("send_coin:"):
        sym = action.split(":", 1)[1]
        st = _send_state.get(uid) or {}
        if sym not in (st.get("coins") or {}):
            try:
                await callback.answer()
            except TelegramBadRequest:
                pass
            return
        st["symbol"] = sym
        st.pop("amount", None)
        st.pop("address", None)
        st.pop("memo", None)
        _send_state[uid] = st
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_amount(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action == "send_max":
        st = _send_state.get(uid) or {}
        sym = st.get("symbol")
        coin = (st.get("coins") or {}).get(sym) if sym else None
        if not coin:
            return
        if coin["fee_sym"] == sym:
            max_amt = max(Decimal("0"), coin["amount"] - coin["fee_amt"])
        else:
            max_amt = coin["amount"]
        st["amount"] = max_amt
        _send_state[uid] = st
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_addr(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action == "send_amount":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_amount(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action in ("send_addr_recent", "send_addr_book"):
        async with SessionLocal() as db:
            user = await ledger.ensure_user(db, uid, uname)
            lang = getattr(user, "language", "ru") or "ru"
        try:
            await callback.answer(t("in_development", lang), show_alert=True)
        except TelegramBadRequest:
            pass
        return

    if action == "send_change_addr":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_addr(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action == "send_memo":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_memo(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action == "send_cancel":
        _send_state.pop(uid, None)
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if action == "send_confirm":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await _render_send_processing(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        asyncio.create_task(_execute_send(bot=bot, uid=uid, uname=uname))
        return

    if action == "send_open_wallet":
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        await render_crypto_main(
            bot=bot, chat_id=chat_id, panel_user_id=uid,
            telegram_id=uid, username=uname,
        )
        return

    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


# ---------------------------------------------------------------------------
# Temporary: /set_gift_emojis — map gift collection names to emojis
# ---------------------------------------------------------------------------

def _parse_gift_emoji_line(line: str) -> tuple[str, str] | None:
    """
    Accepts lines like:
      🐰 https://t.me/nft/JellyBunny-57735
      🐰 JellyBunny
    (Emoji at the START of the line.)
    Returns (collection_prefix, emoji) or None.
    """
    line = line.strip()
    if not line:
        return None
    parts = line.split(None, 1)
    if len(parts) < 2:
        return None
    emoji, raw = parts[0].strip(), parts[1].strip()

    if "t.me/nft/" in raw:
        slug_part = raw.split("t.me/nft/")[-1].split("?")[0]
    else:
        slug_part = raw

    collection = re.split(r"-\d+$", slug_part)[0].strip("/")
    if not collection or not emoji:
        return None
    return collection, emoji


@router.message(Command("set_gift_emojis"))
async def set_gift_emojis_prompt(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
    _pending_gift_emoji_update.add(message.from_user.id)
    await message.answer(
        t("profile.gifts_emoji_prompt", lang),
        parse_mode="HTML",
    )


@router.message(lambda m: bool(m.from_user and m.from_user.id in _pending_gift_emoji_update and m.text))
async def set_gift_emojis_input(message: Message) -> None:
    if not message.from_user or not message.text:
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
    _pending_gift_emoji_update.discard(message.from_user.id)

    added: list[str] = []
    for line in message.text.splitlines():
        parsed = _parse_gift_emoji_line(line)
        if parsed:
            collection, emoji = parsed
            _gift_emoji_map[collection] = emoji
            added.append(f"{emoji} → {collection}")

    if added:
        await message.answer(
            t("profile.gifts_emoji_saved", lang) + "\n" + "\n".join(added),
            parse_mode="HTML",
        )
    else:
        await message.answer(t("profile.gifts_emoji_invalid", lang))


@router.message(Command("set_basic_gifts"))
async def set_basic_gifts_prompt(message: Message) -> None:
    if not message.from_user or not message.bot:
        return
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
    _pending_basic_gifts_update.add(message.from_user.id)
    await message.answer(
        t("profile.basic_gifts_prompt", lang),
        parse_mode="HTML",
    )


@router.message(lambda m: bool(m.from_user and m.from_user.id in _pending_basic_gifts_update and m.text))
async def set_basic_gifts_input(message: Message) -> None:
    if not message.from_user or not message.text:
        return
    _pending_basic_gifts_update.discard(message.from_user.id)

    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, message.from_user.id, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"
        existing = await _ensure_default_basic_gifts(db, user.id)
        by_id: dict[str, tuple[str, Decimal]] = {
            str(item.gift_id): (item.gift_name, Decimal(item.price_usd or 0))
            for item in existing
        }
        changed = 0
        removed = 0
        invalid: list[str] = []

        for raw_line in message.text.splitlines():
            parsed = _parse_basic_gift_line(raw_line)
            if not parsed:
                if raw_line.strip():
                    invalid.append(raw_line.strip())
                continue
            action, gift_id, gift_name, price_usd = parsed
            if action == "delete":
                if gift_id in by_id:
                    by_id.pop(gift_id, None)
                    removed += 1
                continue
            by_id[gift_id] = (gift_name, price_usd)
            changed += 1

        await db.execute(BasicGiftItem.__table__.delete().where(BasicGiftItem.user_id == user.id))
        now = datetime.utcnow()
        for gift_id, (gift_name, price_usd) in by_id.items():
            db.add(
                BasicGiftItem(
                    user_id=user.id,
                    gift_id=gift_id,
                    gift_name=gift_name,
                    price_usd=price_usd,
                    updated_at=now,
                )
            )
        await db.commit()
        _profile_snapshot_cache.pop(user.id, None)

    msg = t("profile.basic_gifts_saved", lang, changed=changed, removed=removed)
    if invalid:
        msg += "\n" + t("profile.basic_gifts_invalid", lang) + "\n" + "\n".join(invalid[:10])
    await message.answer(msg)


@router.message(
    lambda m: bool(
        m.from_user and m.text and (
            m.from_user.id in _pending_send_amount
            or m.from_user.id in _pending_send_addr
            or m.from_user.id in _pending_send_memo
        )
    )
)
async def profile_send_text_input(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    uid = message.from_user.id
    uname = message.from_user.username
    raw = message.text.strip()
    bot = message.bot
    chat_id = message.chat.id

    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, uname)
        lang = getattr(user, "language", "ru") or "ru"

    st = _send_state.get(uid) or {}
    sym = st.get("symbol")
    coin = (st.get("coins") or {}).get(sym) if sym else None

    if uid in _pending_send_amount:
        if not coin:
            _pending_send_amount.discard(uid)
            await _render_send_pick_coin(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
            return
        try:
            amount = Decimal(raw.replace(",", ".").replace(" ", ""))
        except Exception:
            return
        # Проверка лимитов.
        fee_in_same = coin["fee_sym"] == sym
        need = amount + (coin["fee_amt"] if fee_in_same else Decimal("0"))
        if amount < coin["min_amt"] or need > coin["amount"]:
            await _render_send_amount(
                bot=bot, chat_id=chat_id, uid=uid, uname=uname,
                insufficient=True,
            )
            return
        st["amount"] = amount
        _send_state[uid] = st
        _pending_send_amount.discard(uid)
        # Удалим сообщение с цифрой — оно служебное.
        try:
            await message.delete()
        except Exception:
            pass
        await _render_send_addr(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if uid in _pending_send_addr:
        if not _is_valid_ton_address(raw):
            await message.answer(t("crypto.send.addr_invalid", lang))
            return
        st["address"] = raw
        _send_state[uid] = st
        _pending_send_addr.discard(uid)
        try:
            await message.delete()
        except Exception:
            pass
        await _render_send_confirm(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return

    if uid in _pending_send_memo:
        if len(raw) > 50:
            await message.answer(t("crypto.send.memo_too_long", lang))
            return
        st["memo"] = raw
        _send_state[uid] = st
        _pending_send_memo.discard(uid)
        try:
            await message.delete()
        except Exception:
            pass
        await _render_send_confirm(bot=bot, chat_id=chat_id, uid=uid, uname=uname)
        return


@router.message(
    lambda m: bool(
        m.from_user and m.text and (
            m.from_user.id in _pending_crypto_seed_only
            or m.from_user.id in _pending_crypto_addr_only
            or m.from_user.id in _pending_crypto_seed_import
            or m.from_user.id in _pending_crypto_create_addr
            or m.from_user.id in _pending_crypto_rename
        )
    )
)
async def profile_crypto_seed_input(message: Message) -> None:
    if not message.from_user or not message.text or not message.bot:
        return
    uid = message.from_user.id
    raw = message.text.strip()
    async with SessionLocal() as db:
        user = await ledger.ensure_user(db, uid, message.from_user.username)
        lang = getattr(user, "language", "ru") or "ru"

        if uid in _pending_crypto_rename:
            account_id = _pending_crypto_rename.pop(uid)
            account = await ledger.get_account_by_id(db, user.id, account_id)
            if account and raw:
                account.display_name = raw[:255]
                await db.commit()
                _profile_snapshot_cache.pop(user.id, None)
            await render_crypto_main(
                bot=message.bot,
                chat_id=message.chat.id,
                panel_user_id=uid,
                telegram_id=uid,
                username=message.from_user.username,
            )
            return

        if uid in _pending_crypto_addr_only:
            address = raw.strip()
            if not _is_valid_ton_address(address):
                await message.answer(t("crypto.import_addr.invalid", lang))
                return
            account = await ton_service.link_wallet(db, user.id, address)
            # Никакого seed — храним только адрес (view-only).
            await db.commit()
            _pending_crypto_addr_only.discard(uid)
            _profile_snapshot_cache.pop(user.id, None)
            accounts = await ledger.get_active_accounts_by_type(
                db, user.id, AccountType.TON_WALLET,
            )
            for i, a in enumerate(accounts):
                if a.id == account.id:
                    _current_wallet_idx[uid] = i
                    break
            _current_coin_page[uid] = 1
            await render_crypto_main(
                bot=message.bot,
                chat_id=message.chat.id,
                panel_user_id=uid,
                telegram_id=uid,
                username=message.from_user.username,
            )
            return

        if uid in _pending_crypto_seed_only:
            normalized = " ".join(w.strip().lower() for w in raw.split() if w.strip())
            # Новая чистая реализация — все либы перебираются внутри.
            from app.services.wallet_derive import derive_address_from_seed
            address, all_candidates = await derive_address_from_seed(normalized)
            import logging
            logging.getLogger(__name__).info(
                "Seed import: %d candidates, active=%s", len(all_candidates), address,
            )
            if not address:
                # Если ни один кандидат не активен — сохраняем первого по списку
                # (W5R1 от tonutils) как best-effort. Юзер может пополнить и оно
                # активируется в сети.
                if all_candidates:
                    address = all_candidates[0]
                    logging.getLogger(__name__).info(
                        "No active address found, using default candidate: %s", address,
                    )
                else:
                    err_text = (
                        f"<b>❌ {t('crypto.import.not_found', lang)}</b>\n\n"
                        + t("crypto.import.subtitle", lang)
                    )
                    await push_text_panel(
                        bot=message.bot,
                        chat_id=message.chat.id,
                        user_id=uid,
                        text=err_text,
                        reply_markup=crypto_import_prompt_keyboard(lang, back_to="crypto:new_menu"),
                        parse_mode="HTML",
                        disable_web_preview=True,
                    )
                    return
            account = await ton_service.link_wallet(db, user.id, address)
            account.encrypted_secret = _cipher.encrypt(normalized)
            await db.commit()
            _pending_crypto_seed_only.discard(uid)
            _profile_snapshot_cache.pop(user.id, None)
            # Сразу подтягиваем историю и считаем баланс, чтобы юзер сразу
            # увидел данные, а не пустой экран.
            import logging as _logging
            _slog = _logging.getLogger(__name__)
            try:
                inserted = await ton_service.sync_transactions(db, account)
                _slog.info("Seed import sync: address=%s, txs=%d", address, inserted)
            except Exception as exc:
                _slog.warning("Seed import sync failed for %s: %s", address, exc)
            try:
                live_bal = await ton_service.get_live_balance_ton(account)
                jets = await ton_service.get_jettons_detailed(account)
                _slog.info(
                    "Seed import balance: address=%s, TON=%s, jettons=%d",
                    address, live_bal, len(jets),
                )
            except Exception as exc:
                _slog.warning("Seed import balance fetch failed: %s", exc)
            # Удаляем сообщение пользователя с seed-фразой — она не должна
            # оставаться в чате в открытом виде.
            try:
                await message.delete()
            except Exception:
                pass
            # Open the just-imported wallet.
            accounts = await ledger.get_active_accounts_by_type(
                db, user.id, AccountType.TON_WALLET
            )
            for i, a in enumerate(accounts):
                if a.id == account.id:
                    _current_wallet_idx[uid] = i
                    break
            _current_coin_page[uid] = 1
            await render_crypto_main(
                bot=message.bot,
                chat_id=message.chat.id,
                panel_user_id=uid,
                telegram_id=uid,
                username=message.from_user.username,
            )
            return

        if uid in _pending_crypto_seed_import:
            parts = [p.strip() for p in raw.split("|", 1)]
            if len(parts) != 2:
                await message.answer(t("profile.crypto.import_invalid", lang))
                return
            seed_phrase, address = parts[0], parts[1]
            if not _looks_like_seed_phrase(seed_phrase) or not _is_valid_ton_address(address):
                await message.answer(t("profile.crypto.import_invalid", lang))
                return
            account = await ton_service.link_wallet(db, user.id, address)
            account.encrypted_secret = _cipher.encrypt(seed_phrase)
            await db.commit()
            _pending_crypto_seed_import.discard(uid)
            # Удаляем сообщение пользователя с seed-фразой.
            try:
                await message.delete()
            except Exception:
                pass
            await message.answer(t("profile.crypto.import_saved", lang))
        elif uid in _pending_crypto_create_addr:
            seed_phrase = _pending_crypto_create_addr[uid]
            parts = [p.strip() for p in raw.split("|", 1)]
            address = parts[1] if len(parts) == 2 and parts[0].lower() == "address" else raw
            if not _is_valid_ton_address(address):
                await message.answer(t("profile.crypto.create_invalid_addr", lang))
                return
            account = await ton_service.link_wallet(db, user.id, address)
            account.encrypted_secret = _cipher.encrypt(seed_phrase)
            await db.commit()
            _pending_crypto_create_addr.pop(uid, None)
            await message.answer(t("profile.crypto.create_saved", lang))

    await _render_crypto_wallet_view(
        bot=message.bot,
        chat_id=message.chat.id,
        panel_user_id=uid,
        telegram_id=uid,
        username=message.from_user.username,
    )
