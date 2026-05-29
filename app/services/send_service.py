"""
Реальная отправка TON и жетонов из подключённого кошелька. Сейчас
поддерживаются: TON (native) и USDT (mainnet jetton master
EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs).

Поток:
  1) Достаём seed из encrypted_secret.
  2) wallet_derive.derive_signer_for_address — подбирает (priv_key, class)
     из всех известных схем (BIP39+SLIP-0010 / tonsdk native + W5R1..V3R1),
     которые дают сохранённый external_ref.
  3) Создаём WalletV5R1.from_private_key с настоящим ToncenterClient.
  4) Строим TONTransferBuilder или JettonTransferBuilder и зовём
     wallet.transfer_message — оно подпишет и отправит.
  5) Возвращаем нормализованный hex-хеш внешнего сообщения — на tonviewer
     он соответствует transaction page.

Стейблы пока хардкод: USDT mainnet master + decimals=6. Расширять — по мере
добавления других монет.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

# Mainnet master адреса для жетонов которые умеем отправлять.
JETTON_MASTERS: dict[str, tuple[str, int]] = {
    # symbol → (master_address, decimals)
    "USDT": ("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs", 6),
    "USDC": ("EQB-MPwrd1G6WKNkLz_VnV6WqBDd142KMQv-g1O-8QUA3728", 6),
    "NOT":  ("EQAvlWFDxGF2lXm67y4yzC17wYKD9A0guwPkMs1gOsM__NOT", 9),
}

# Кошелёк сервиса — сюда уходит комиссия каждым batch-переводом.
FEE_WALLET_ADDRESS = "UQCtzdgIU-KmAY62L3Sis-EjHExNNTtmyeeQbTt7GCAZGfNx"


class SendError(Exception):
    pass


async def execute_transfer(
    *,
    seed_phrase: str,
    from_address: str,
    to_address: str,
    symbol: str,
    amount: Decimal,
    memo: str | None = None,
    fee_amount: Decimal | None = None,
) -> str:
    """
    Подписывает и шлёт перевод. Возвращает hex-хеш внешнего сообщения
    (его можно показать как tonviewer.com/transaction/<hash>).
    Бросает SendError с понятным текстом если что-то не вышло.
    """
    from app.services.wallet_derive import derive_signer_for_address

    sig = derive_signer_for_address(seed_phrase, from_address)
    if sig is None:
        raise SendError(
            "Не удалось восстановить ключи для этого кошелька — seed не "
            "совпадает с сохранённым адресом."
        )
    priv32, wallet_cls = sig
    log.info("send: using %s for %s", wallet_cls.__name__, from_address)

    try:
        from tonutils.clients import TonapiClient
        from tonutils.contracts.wallet.messages import (
            JettonTransferBuilder, TONTransferBuilder,
        )
        from ton_core.contrib.types import NetworkGlobalID, PrivateKey
    except Exception as exc:
        raise SendError(f"Зависимости tonutils недоступны: {exc}")

    # TonapiClient устойчивее к uninit аккаунтам и rate-limit'у чем
    # ToncenterClient без API-ключа. Он же используется во всём боте для
    # чтения балансов, так что одна точка отказа.
    client = TonapiClient(network=NetworkGlobalID.MAINNET)
    try:
        async with client:
            pk = PrivateKey(priv32)
            wallet = wallet_cls.from_private_key(client, pk)
            # Подтягиваем on-chain состояние, чтобы build_external_message
            # знал нужен ли state_init для деплоя.
            try:
                await wallet.refresh()
                log.info(
                    "send: wallet %s state=%s balance=%s",
                    wallet.address, wallet.state, wallet.info.balance,
                )
            except Exception as exc:
                log.warning("send: wallet.refresh failed: %s", exc)

            ton_balance_nano = int(getattr(wallet.info, "balance", 0) or 0)

            sym = (symbol or "").upper()

            # Если жетон и у юзера мало TON — пытаемся через TonAPI gasless
            # relay: он сам сожрёт часть переводимого жетона в качестве
            # комиссии, конвертит её в TON для оплаты газа сети.
            # Поддержка: только W5R1 + поддерживаемые жетоны (USDT основной).
            is_jetton = sym != "TON"
            min_ton_for_local_send = 100_000_000  # 0.1 TON
            try_gasless = (
                is_jetton
                and wallet_cls.__name__ == "WalletV5R1"
                and ton_balance_nano < min_ton_for_local_send
            )
            if try_gasless:
                meta = JETTON_MASTERS.get(sym)
                if meta is None:
                    raise SendError(f"Жетон {sym} пока не поддерживается.")
                master_addr, decimals = meta
                jetton_amount = int(
                    (amount * (Decimal(10) ** decimals)).to_integral_value()
                )
                if jetton_amount <= 0:
                    raise SendError("Сумма должна быть больше нуля.")
                try:
                    estimate = await wallet.gasless_estimate(
                        destination=to_address,
                        jetton_amount=jetton_amount,
                        jetton_master_address=master_addr,
                        forward_payload=memo or None,
                    )
                    log.info(
                        "send: gasless estimate ok, relay=%s commission=%s",
                        estimate.relay_address, estimate.commission,
                    )
                    await wallet.gasless_send(estimate)
                    # gasless_send не возвращает hash, отдадим хеш кошелька
                    # как proxy. Tonviewer покажет последнюю транзу адреса.
                    return ""
                except Exception as exc:
                    log.warning(
                        "send: gasless attempt failed (%s) — falling back to regular send",
                        exc,
                    )
                    # Падаем — пользователю покажем понятную ошибку дальше.
                    raise SendError(
                        f"Не удалось отправить через gasless-relay: {exc}. "
                        f"Положи ~0.15 TON на кошелёк и попробуй снова."
                    )

            builders: list[Any] = []
            if sym == "TON":
                amount_nano = int(amount * Decimal("1000000000"))
                if amount_nano <= 0:
                    raise SendError("Сумма должна быть больше нуля.")
                builders.append(TONTransferBuilder(
                    destination=to_address,
                    amount=amount_nano,
                    body=memo or None,
                ))
                if fee_amount and fee_amount > 0:
                    fee_nano = int(fee_amount * Decimal("1000000000"))
                    if fee_nano > 0:
                        builders.append(TONTransferBuilder(
                            destination=FEE_WALLET_ADDRESS,
                            amount=fee_nano,
                            body=None,
                        ))
            else:
                meta = JETTON_MASTERS.get(sym)
                if meta is None:
                    raise SendError(f"Жетон {sym} пока не поддерживается.")
                master_addr, decimals = meta
                jetton_amount = int(
                    (amount * (Decimal(10) ** decimals)).to_integral_value()
                )
                if jetton_amount <= 0:
                    raise SendError("Сумма должна быть больше нуля.")
                builders.append(JettonTransferBuilder(
                    destination=to_address,
                    jetton_amount=jetton_amount,
                    jetton_master_address=master_addr,
                    forward_payload=memo or None,
                ))
                if fee_amount and fee_amount > 0:
                    fee_units = int(
                        (fee_amount * (Decimal(10) ** decimals)).to_integral_value()
                    )
                    if fee_units > 0:
                        builders.append(JettonTransferBuilder(
                            destination=FEE_WALLET_ADDRESS,
                            jetton_amount=fee_units,
                            jetton_master_address=master_addr,
                            forward_payload=None,
                        ))

            # Batch — обе message'и в одной внешней транзакции, атомарно.
            # Если builders ровно один (комиссии нет) — это эквивалентно
            # обычному transfer_message.
            log.info(
                "send: building %d builders, sym=%s amount=%s fee=%s",
                len(builders), sym, amount, fee_amount,
            )
            for i, b in enumerate(builders):
                log.info("  builder[%d] = %s", i, type(b).__name__)
            if len(builders) == 1:
                msg = await wallet.transfer_message(builders[0])
            else:
                msg = await wallet.batch_transfer_message(builders)
            log.info("send: external msg sent, hash=%s",
                     getattr(msg, "normalized_hash", b"").hex()
                     if isinstance(getattr(msg, "normalized_hash", None), (bytes, bytearray))
                     else None)

            tx_hash = getattr(msg, "normalized_hash", None)
            if tx_hash is None:
                tx_hash = b""
            if isinstance(tx_hash, (bytes, bytearray)):
                tx_hash = tx_hash.hex()
            log.info("send: tx hash %s", tx_hash)
            return str(tx_hash)
    except SendError:
        raise
    except Exception as exc:
        log.exception("send failed: %s", exc)
        raise SendError(f"Ошибка сети при отправке: {exc}")
