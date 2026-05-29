from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, AccountType, Currency, Transaction, TransactionType


STABLECOIN_SYMBOLS = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "USD₮"}


class TonService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def link_wallet(self, db: AsyncSession, user_id: int, wallet_address: str) -> Account:
        account = (
            await db.execute(
                select(Account).where(
                    and_(
                        Account.user_id == user_id,
                        Account.account_type == AccountType.TON_WALLET,
                        Account.external_ref == wallet_address,
                    )
                )
            )
        ).scalars().first()
        if not account:
            # Назначаем следующий по порядку sort_order и помечаем избранным,
            # если это первый кошелёк юзера. Так свежий импорт сразу становится
            # «главным», если других нет.
            from sqlalchemy import func as _func
            max_order = (
                await db.execute(
                    select(_func.coalesce(_func.max(Account.sort_order), 0)).where(
                        Account.user_id == user_id,
                        Account.account_type == AccountType.TON_WALLET,
                    )
                )
            ).scalar_one()
            has_any = (
                await db.execute(
                    select(Account.id).where(
                        Account.user_id == user_id,
                        Account.account_type == AccountType.TON_WALLET,
                        Account.is_active.is_(True),
                    ).limit(1)
                )
            ).scalar_one_or_none()
            account = Account(
                user_id=user_id,
                account_type=AccountType.TON_WALLET,
                display_name="TON wallet",
                external_ref=wallet_address,
                sort_order=int(max_order or 0) + 1,
                is_favorite=not has_any,
            )
            db.add(account)
        account.is_active = True
        await db.commit()
        await db.refresh(account)
        return account

    async def unlink_wallet(self, db: AsyncSession, user_id: int, wallet_address: str) -> None:
        account = (
            await db.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.account_type == AccountType.TON_WALLET,
                    Account.external_ref == wallet_address,
                )
            )
        ).scalars().first()
        if account:
            account.is_active = False
            await db.commit()

    async def get_active_account(self, db: AsyncSession, user_id: int) -> Account | None:
        return (
            await db.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.account_type == AccountType.TON_WALLET,
                    Account.is_active.is_(True),
                )
            )
        ).scalars().first()

    async def sync_transactions(self, db: AsyncSession, account: Account) -> int:
        """
        Парсит /accounts/{addr}/events tonapi и записывает новые TonTransfer
        и JettonTransfer (USDT/USDC/…) в transactions. Возвращает количество
        вставленных строк. Новые income-строки сохраняются с notified=False
        — их подхватит фоновый воркер уведомлений.
        """
        inserted_objs = await self._sync_via_events(db, account)
        await db.commit()
        return len(inserted_objs)

    async def _sync_via_events(
        self, db: AsyncSession, account: Account,
    ) -> list[Transaction]:
        addr = account.external_ref
        url = f"{self.settings.ton_api_url}/accounts/{addr}/events"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url, params={"limit": 50})
                response.raise_for_status()
                events = response.json().get("events", []) or []
        except (httpx.HTTPError, ValueError):
            return []

        owner_addrs = {self._normalize(addr)}
        inserted: list[Transaction] = []
        for event in events:
            event_id = event.get("event_id")
            if not event_id:
                continue
            actions = event.get("actions") or []
            timestamp = event.get("timestamp")
            for ai, action in enumerate(actions):
                # Уникальный external_tx_id: event_id + индекс action,
                # т.к. одно событие может содержать несколько переводов.
                ext_id = f"{event_id}#{ai}"
                exists = (
                    await db.execute(
                        select(Transaction.id).where(Transaction.external_tx_id == ext_id)
                    )
                ).scalar_one_or_none()
                if exists:
                    continue
                parsed = self._parse_action(action, owner_addrs)
                if parsed is None:
                    continue
                amount, currency, direction, description = parsed
                created_at = (
                    datetime.utcfromtimestamp(timestamp)
                    if isinstance(timestamp, int) else datetime.utcnow()
                )
                tx = Transaction(
                    user_id=account.user_id,
                    account_id=account.id,
                    tx_type=direction,
                    amount=amount,
                    currency=currency,
                    category="crypto",
                    description=description,
                    external_tx_id=ext_id,
                    created_at=created_at,
                    notified=(direction != TransactionType.INCOME),
                )
                db.add(tx)
                inserted.append(tx)
        return inserted

    @staticmethod
    def _normalize(addr: str | None) -> str:
        """
        Канонизируем адрес в hex hash_part через pytoniq_core.Address.
        Это умеет парсить и raw ('0:hex…'), и friendly (UQ/EQ base64url) форму
        и приводит всё к одному хешу. Без этого сравнение чужого 'recipient'
        от tonapi (raw hex) с нашим external_ref (UQ-base64) НИКОГДА не
        совпадало → всё помечалось EXPENSE и уведомления не шли.
        """
        if not addr:
            return ""
        try:
            from pytoniq_core.boc.address import Address
            a = Address(addr.strip())
            # hash_part — bytes(32). Сравниваем по hex.
            return a.hash_part.hex()
        except Exception:
            return addr.strip().lower()

    def _parse_action(
        self, action: dict, owner_addrs: set[str],
    ) -> tuple[Decimal, Currency, TransactionType, str] | None:
        atype = action.get("type")
        if atype == "TonTransfer":
            data = action.get("TonTransfer") or {}
            recipient = (data.get("recipient") or {}).get("address") or ""
            sender = (data.get("sender") or {}).get("address") or ""
            raw_amount = int(data.get("amount", 0) or 0)
            amount = Decimal(str(raw_amount)) / Decimal("1000000000")
            is_income = self._normalize(recipient) in owner_addrs
            return (
                amount,
                Currency.TON,
                TransactionType.INCOME if is_income else TransactionType.EXPENSE,
                (data.get("comment") or "ton transfer")[:255],
            )
        if atype == "JettonTransfer":
            data = action.get("JettonTransfer") or {}
            jetton = data.get("jetton") or {}
            symbol = (jetton.get("symbol") or "").upper().replace("₮", "T")
            try:
                decimals = int(jetton.get("decimals", 9))
            except Exception:
                decimals = 9
            try:
                raw_amount = int(data.get("amount", 0) or 0)
            except Exception:
                raw_amount = 0
            amount = Decimal(str(raw_amount)) / Decimal(10 ** decimals)
            recipient = (data.get("recipient") or {}).get("address") or ""
            is_income = self._normalize(recipient) in owner_addrs
            try:
                currency = Currency(symbol)
            except ValueError:
                # Неизвестный жетон — пропускаем, чтобы не плодить мусорные строки.
                return None
            return (
                amount,
                currency,
                TransactionType.INCOME if is_income else TransactionType.EXPENSE,
                (data.get("comment") or f"{symbol} transfer")[:255],
            )
        return None

    async def get_live_balance_ton(self, account: Account) -> Decimal | None:
        url = f"{self.settings.ton_api_url}/blockchain/accounts/{account.external_ref}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        raw_balance = payload.get("balance")
        if raw_balance is None:
            return None
        return Decimal(str(raw_balance)) / Decimal("1000000000")

    async def get_jettons_detailed(self, account: Account) -> list[dict]:
        """
        Returns [{symbol, amount, usd_value}] for every jetton with positive balance.
        usd_value is None when API has no price for the token.
        """
        url = f"{self.settings.ton_api_url}/accounts/{account.external_ref}/jettons"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, params={"currencies": "usd"})
                if response.status_code != 200:
                    return []
                balances = response.json().get("balances", [])
        except (httpx.HTTPError, ValueError):
            return []

        results: list[dict] = []
        for entry in balances:
            jetton = entry.get("jetton", {}) or {}
            symbol = jetton.get("symbol", "")
            decimals = int(jetton.get("decimals", 9))
            try:
                amount = Decimal(str(entry.get("balance", "0"))) / Decimal(10 ** decimals)
            except Exception:
                continue
            if amount <= 0:
                continue
            usd_value: Decimal | None = None
            if symbol.upper() in STABLECOIN_SYMBOLS or symbol == "USD₮":
                usd_value = amount
            else:
                price_info = entry.get("price") or {}
                prices = (price_info.get("prices") or {}) if isinstance(price_info, dict) else {}
                usd_price = prices.get("USD") or prices.get("usd")
                if usd_price:
                    try:
                        usd_value = amount * Decimal(str(usd_price))
                    except Exception:
                        usd_value = None
            results.append({"symbol": symbol, "amount": amount, "usd_value": usd_value})
        return results

    async def get_jetton_balances_usd(self, account: Account) -> tuple[list[tuple[str, Decimal]], Decimal]:
        """
        Returns ([(symbol, amount), ...], total_usd) for jetton balances.
        Stablecoins counted 1:1 USD; other tokens skipped for now.
        """
        url = f"{self.settings.ton_api_url}/accounts/{account.external_ref}/jettons"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    return [], Decimal("0")
                balances = response.json().get("balances", [])
        except (httpx.HTTPError, ValueError):
            return [], Decimal("0")

        items: list[tuple[str, Decimal]] = []
        total_usd = Decimal("0")
        for entry in balances:
            raw = entry.get("balance", "0")
            jetton = entry.get("jetton", {})
            symbol = jetton.get("symbol", "")
            decimals = int(jetton.get("decimals", 9))
            amount = Decimal(str(raw)) / Decimal(10 ** decimals)
            if amount < Decimal("0.01"):
                continue
            if symbol.upper() in STABLECOIN_SYMBOLS or symbol == "USD₮":
                items.append((symbol, amount))
                total_usd += amount
        return items, total_usd

    async def ton_price_usd(self) -> Decimal | None:
        # Fallback public price source for TON/USD.
        url = "https://api.coingecko.com/api/v3/simple/price"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, params={"ids": "the-open-network", "vs_currencies": "usd"})
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        usd = data.get("the-open-network", {}).get("usd")
        return Decimal(str(usd)) if usd is not None else None

    async def get_user_nfts(self, wallet_address: str) -> list[dict]:
        """
        Fetches all NFTs owned by the wallet address.
        """
        url = f"{self.settings.ton_api_url}/accounts/{wallet_address}/nfts"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url, params={"limit": 100})
                response.raise_for_status()
                return response.json().get("nft_items", [])
        except (httpx.HTTPError, ValueError) as e:
            print(f"Error fetching NFTs for {wallet_address}: {e}")
            return []

    async def get_telegram_gifts(self, wallet_address: str) -> list[str]:
        """
        Filters NFTs to find only Telegram Gifts and returns their slugs/names.
        Official Telegram Gifts collection: EQCA14o1-VWhS2efqoh_9M1b_A9DtKTfS9pM-9n1S_pPQZ2Z
        """
        # Note: In a real scenario, we'd verify the collection address.
        # For now, we'll look for NFTs that have 'Gift' or similar in metadata 
        # or belong to the known collection.
        GIFTS_COLLECTION = "0:82d78a35f955a14b679fa88e7f335bf00f43b4a4df4bda4cf9df54bfa4f419d9" # Hex for EQCA14o1...
        
        nfts = await self.get_user_nfts(wallet_address)
        gift_slugs = []
        for nft in nfts:
            col = nft.get("collection", {})
            if col.get("address") == GIFTS_COLLECTION or "Gift" in nft.get("metadata", {}).get("name", ""):
                # Try to extract the slug from metadata or name
                name = nft.get("metadata", {}).get("name")
                if name:
                    # Slugs are usually like "PlushPepe-274"
                    # If name is "Plush Pepe #274", we convert it.
                    slug = name.replace(" #", "-").replace(" ", "")
                    gift_slugs.append(slug)
        return gift_slugs
