"""
Чистая реализация деривации TON-адресов из seed-фразы.

Используем несколько источников чтоб максимально покрыть разные приложения:
  * tonutils — современная либа с поддержкой W5R1 (Tonkeeper, MyTonWallet)
  * tonsdk — для классических V3R2 / V4R2 (старый Tonkeeper, @wallet, Tonhub)
  * BIP39 через bip_utils — для seed'ов в BIP39 формате (Trust, MyTonWallet legacy)

Бросаем все варианты на блокчейн (tonapi.io) и берём тот, который реально
существует и активен. Если все пустые — возвращаем дефолт V5R1.
"""

from __future__ import annotations

import logging
from typing import Iterable

import httpx

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# Деривация: пробуем несколько библиотек и собираем все кандидаты
# ────────────────────────────────────────────────────────────────

def _try_tonutils(words: list[str]) -> list[str]:
    """Через tonutils — поддержка V5R1 (Tonkeeper W5)."""
    addrs: list[str] = []
    if len(words) != 24:
        return addrs
    try:
        from tonutils.wallet import WalletV5R1, WalletV4R2, WalletV3R2
        # tonutils.WalletV5R1 умеет from_mnemonic без живого клиента — он
        # вычисляет адрес локально по pub_key. Передаём None как client.
        for cls in (WalletV5R1, WalletV4R2, WalletV3R2):
            try:
                wallet, _, _, _ = cls.from_mnemonic(client=None, mnemonic=words)
                addr = wallet.address.to_str(
                    is_user_friendly=True, is_bounceable=False, is_url_safe=True,
                )
                addrs.append(addr)
            except Exception as exc:
                log.info("tonutils %s failed: %s", cls.__name__, exc)
    except ImportError:
        log.warning("tonutils not installed")
    return addrs


def _try_tonsdk(words: list[str]) -> list[str]:
    """Через tonsdk — V3R2 / V4R1 / V4R2."""
    addrs: list[str] = []
    if len(words) != 24:
        return addrs
    try:
        from tonsdk.contract.wallet import Wallets, WalletVersionEnum
        for ver in (WalletVersionEnum.v4r2, WalletVersionEnum.v3r2,
                    WalletVersionEnum.v4r1, WalletVersionEnum.v3r1):
            try:
                _, _, _, w = Wallets.from_mnemonics(words, ver, 0)
                # Two variants: bounceable (EQ) and non-bounceable (UQ)
                addrs.append(w.address.to_string(True, True, True))   # EQ
                addrs.append(w.address.to_string(True, True, False))  # raw url-unsafe?
            except Exception as exc:
                log.info("tonsdk %s failed: %s", ver, exc)
    except Exception as exc:
        log.warning("tonsdk import failed: %s", exc)
    return addrs


def _try_bip39(words: list[str]) -> list[str]:
    """Через BIP39 → BIP44 m/44'/607'/0' — Trust Wallet и пр."""
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
    """
    Возвращает уникальные адреса-кандидаты со всех известных источников.
    Чем больше — тем выше шанс что какой-то из них активен на блокчейне.
    """
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


# ────────────────────────────────────────────────────────────────
# Проверка кандидатов через tonapi.io
# ────────────────────────────────────────────────────────────────

async def find_active_address(candidates: list[str]) -> str | None:
    """
    Спрашивает у tonapi.io — какой из этих адресов реально активен в сети
    (статус active или баланс > 0 или есть жетоны). Возвращает первый
    активный или None.
    """
    if not candidates:
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        for addr in candidates:
            # 1. Базовый статус
            try:
                r = await client.get(
                    f"https://tonapi.io/v2/blockchain/accounts/{addr}"
                )
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("status", "")
                    bal = int(data.get("balance", 0))
                    if status == "active" or bal > 0:
                        return addr
            except Exception:
                pass
            # 2. Жетоны (даже если основной контракт не активирован, USDT может быть)
            try:
                r = await client.get(
                    f"https://tonapi.io/v2/accounts/{addr}/jettons"
                )
                if r.status_code == 200:
                    balances = r.json().get("balances") or []
                    if any(int(b.get("balance", 0)) > 0 for b in balances):
                        return addr
            except Exception:
                pass
    return None


# ────────────────────────────────────────────────────────────────
# Главная точка входа
# ────────────────────────────────────────────────────────────────

async def derive_address_from_seed(seed: str) -> tuple[str | None, list[str]]:
    """
    Возвращает (active_address, all_candidates).
      * active_address — адрес который реально на блокчейне (или None)
      * all_candidates — все варианты которые мы пробовали (для UI/логов)
    """
    candidates = derive_all_candidates(seed)
    if not candidates:
        return None, []
    active = await find_active_address(candidates)
    return active, candidates
