"""
Деривация TON-адресов из seed-фразы.

Перебираем все распространённые версии кошельков (W5R1 — дефолт Tonkeeper /
MyTonWallet, V4R2 — старый Tonkeeper и @wallet, V3R2 — старые клиенты), плюс
BIP39 (Trust, MyTonWallet legacy). Все варианты проверяем у tonapi.io —
берём первый адрес, у которого есть on-chain состояние (active или ненулевой
баланс/жетоны). Если ни один не активен — отдаём дефолтного кандидата W5R1.
"""

from __future__ import annotations

import logging
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


# tonutils >=2 требует ClientProtocol с .network. Реальный клиент при деривации
# не используется — адрес считается локально из pub_key. Поэтому держим один
# тонкий синглтон-клиент и переиспользуем.
def _mainnet_client():
    from tonutils.clients import ToncenterClient
    from ton_core.contrib.types import NetworkGlobalID
    return ToncenterClient(network=NetworkGlobalID.MAINNET)


def _addr_uq(address) -> str:
    """UQ-формат (non-bounceable, url-safe) — как сохраняет Tonkeeper по умолчанию."""
    return address.to_str(is_user_friendly=True, is_bounceable=False, is_url_safe=True)


def _addr_eq(address) -> str:
    """EQ-формат (bounceable, url-safe)."""
    return address.to_str(is_user_friendly=True, is_bounceable=True, is_url_safe=True)


def _try_tonutils(words: list[str]) -> list[str]:
    """V5R1 / V4R2 / V3R2 через tonutils v2.x."""
    addrs: list[str] = []
    if len(words) != 24:
        return addrs
    try:
        from tonutils.contracts.wallet.versions.v5 import WalletV5R1, WalletV5Beta
        from tonutils.contracts.wallet.versions.v4 import WalletV4R2, WalletV4R1
        from tonutils.contracts.wallet.versions.v3 import WalletV3R2, WalletV3R1
        from tonutils.contracts.wallet.versions.v2 import WalletV2R2, WalletV2R1
        from tonutils.contracts.wallet.versions.v1 import WalletV1R3, WalletV1R2, WalletV1R1
    except ImportError as exc:
        log.warning("tonutils not available: %s", exc)
        return addrs

    client = _mainnet_client()
    for cls in (
        WalletV5R1, WalletV5Beta,
        WalletV4R2, WalletV4R1,
        WalletV3R2, WalletV3R1,
        WalletV2R2, WalletV2R1,
        WalletV1R3, WalletV1R2, WalletV1R1,
    ):
        try:
            wallet, _, _, _ = cls.from_mnemonic(client, words, validate=False)
            # И UQ, и EQ — на tonapi оба ссылаются на один аккаунт, но мы хотим
            # сохранить именно UQ (как у Tonkeeper) если этот адрес активен.
            addrs.append(_addr_uq(wallet.address))
            addrs.append(_addr_eq(wallet.address))
        except Exception as exc:
            log.info("tonutils %s failed: %s", cls.__name__, exc)
    return addrs


def _try_tonsdk(words: list[str]) -> list[str]:
    """Старые версии через tonsdk — дублирующий путь на случай если tonutils лёг."""
    addrs: list[str] = []
    if len(words) != 24:
        return addrs
    try:
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum
    except Exception as exc:
        log.info("tonsdk import failed: %s", exc)
        return addrs

    for ver in (WalletVersionEnum.v4r2, WalletVersionEnum.v3r2,
                WalletVersionEnum.v4r1, WalletVersionEnum.v3r1):
        try:
            _, _, _, w = Wallets.from_mnemonics(words, ver, 0)
            addrs.append(w.address.to_string(True, True, True))   # EQ url-safe
            addrs.append(w.address.to_string(True, True, False))  # EQ
        except Exception as exc:
            log.info("tonsdk %s failed: %s", ver, exc)
    return addrs


def _try_bip39(words: list[str]) -> list[str]:
    """BIP39 → BIP44 m/44'/607'/0' — Trust Wallet и пр."""
    addrs: list[str] = []
    if len(words) not in {12, 15, 18, 21, 24}:
        return addrs
    try:
        from bip_utils import (
            Bip39MnemonicValidator, Bip39SeedGenerator, Bip44, Bip44Coins,
        )
        phrase = " ".join(words)
        if not Bip39MnemonicValidator().IsValid(phrase):
            return addrs
        seed_bytes = Bip39SeedGenerator(phrase).Generate()
        acc = Bip44.FromSeed(seed_bytes, Bip44Coins.TON).DeriveDefaultPath()
        addrs.append(acc.PublicKey().ToAddress())
    except Exception as exc:
        log.info("bip39 derivation failed: %s", exc)
    return addrs


def derive_all_candidates(seed: str) -> list[str]:
    """Уникальные адреса-кандидаты из всех известных схем деривации."""
    words = [w.strip().lower() for w in (seed or "").split() if w.strip()]
    if not words:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for addr in (
        _try_tonutils(words) + _try_tonsdk(words) + _try_bip39(words)
    ):
        if addr and addr not in seen:
            seen.add(addr)
            out.append(addr)
    log.info("derived %d unique candidates from seed", len(out))
    return out


async def _score_address(client: httpx.AsyncClient, addr: str) -> tuple[float, int]:
    """
    Возвращает (usd_value, tx_count) для адреса.
      * usd_value — баланс TON в нанотонах + сумма стейблов в USD (1:1)
      * tx_count — число транзакций (для tie-break)
    Если адрес пустой и без истории — оба нуля.
    """
    usd_value = 0.0
    tx_count = 0
    # 1. TON-баланс (в нанотонах — как «вес»; для сравнения этого достаточно).
    try:
        r = await client.get(f"https://tonapi.io/v2/accounts/{addr}")
        if r.status_code == 200:
            data = r.json()
            status = (data.get("status") or "").lower()
            bal_nano = int(data.get("balance", 0) or 0)
            # Грубо: 1 TON ≈ $3 (нам не нужен точный курс, нужно сравнить кошельки
            # одной seed между собой; масштаб одинаков).
            usd_value += (bal_nano / 1e9) * 3.0
            log.info(
                "score[%s] status=%s ton_balance=%.6f",
                addr[:12], status, bal_nano / 1e9,
            )
    except Exception as exc:
        log.info("score[%s] account fetch failed: %s", addr[:12], exc)
    # 2. Жетоны — стейблы считаем 1:1, остальное прикидываем по price.prices.USD.
    try:
        r = await client.get(
            f"https://tonapi.io/v2/accounts/{addr}/jettons",
            params={"currencies": "usd"},
        )
        if r.status_code == 200:
            balances = r.json().get("balances") or []
            for entry in balances:
                jet = entry.get("jetton") or {}
                symbol = (jet.get("symbol") or "").upper()
                try:
                    decimals = int(jet.get("decimals", 9))
                    amt = int(entry.get("balance", 0) or 0) / (10 ** decimals)
                except Exception:
                    continue
                if amt <= 0:
                    continue
                if symbol in {"USDT", "USDC", "DAI", "USD₮"} or "USD" in symbol:
                    usd_value += amt
                else:
                    price = (entry.get("price") or {}).get("prices") or {}
                    usd_price = price.get("USD") or price.get("usd")
                    if usd_price:
                        try:
                            usd_value += amt * float(usd_price)
                        except Exception:
                            pass
            if balances:
                log.info("score[%s] jettons=%d", addr[:12], len(balances))
    except Exception as exc:
        log.info("score[%s] jettons fetch failed: %s", addr[:12], exc)
    # 3. История — как tie-break, если балансов нет, но кошелёк когда-то использовался.
    try:
        r = await client.get(
            f"https://tonapi.io/v2/blockchain/accounts/{addr}/transactions",
            params={"limit": 50},
        )
        if r.status_code == 200:
            txs = r.json().get("transactions") or []
            tx_count = len(txs)
            if tx_count:
                log.info("score[%s] tx_count=%d", addr[:12], tx_count)
    except Exception as exc:
        log.info("score[%s] tx fetch failed: %s", addr[:12], exc)
    return usd_value, tx_count


async def find_active_address(candidates: list[str]) -> str | None:
    """
    Прогоняет ВСЕ кандидаты и выбирает тот, у которого реально больше всего:
    сперва по USD-стоимости (TON+жетоны), при равенстве — по числу транзакций.
    Из одной seed-фразы у юзера часто несколько кошельков (W5R1, V4R2, V3R2);
    наша задача — угадать «основной» = с деньгами.
    """
    if not candidates:
        return None
    headers = {"User-Agent": "MoneyTgBot/1.0 (+wallet-derive)"}
    best_addr: str | None = None
    best_score: tuple[float, int] = (0.0, 0)
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        for addr in candidates:
            score = await _score_address(client, addr)
            log.info("addr[%s] score=%s", addr[:12], score)
            if score > best_score:
                best_score = score
                best_addr = addr
    if best_addr is not None and best_score > (0.0, 0):
        log.info("picked active address %s with score %s", best_addr, best_score)
        return best_addr
    return None


async def derive_address_from_seed(seed: str) -> tuple[str | None, list[str]]:
    """
    (active_address, all_candidates).
      * active_address — реально существующий на блокчейне (или None)
      * all_candidates — все варианты, которые проверяли
    """
    candidates = derive_all_candidates(seed)
    if not candidates:
        return None, []
    active = await find_active_address(candidates)
    return active, candidates
