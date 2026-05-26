from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Account, AccountType, Currency, Transaction, TransactionType
from app.services.security import SecretCipher


# MCC code → human-readable category. Curated for the most common codes; unknown
# codes fall through to the generic "Покупка" / "Перевод" labels.
_MCC_CATEGORIES: dict[int, str] = {
    # ATM / cash
    6010: "Снятие наличных",
    6011: "Снятие наличных",
    # Transfers
    4829: "Перевод",
    6012: "Перевод",
    6536: "Перевод",
    6538: "Перевод",
    6540: "Перевод",
    # Telecoms
    4812: "Связь",
    4814: "Связь",
    4815: "Связь",
    # Utilities
    4900: "Коммунальные",
    # Travel
    4111: "Транспорт",
    4112: "Транспорт",
    4121: "Такси",
    4131: "Транспорт",
    4411: "Круиз",
    4511: "Авиа",
    4722: "Турагентство",
    4789: "Транспорт",
    5541: "АЗС",
    5542: "АЗС",
    7011: "Отель",
    7512: "Аренда авто",
    7513: "Аренда авто",
    # Food
    5411: "Продукты",
    5422: "Продукты",
    5441: "Сладости",
    5451: "Молочное",
    5462: "Пекарня",
    5499: "Продукты",
    5811: "Кафе",
    5812: "Кафе/ресторан",
    5813: "Бар",
    5814: "Фастфуд",
    # Alcohol
    5921: "Алкоголь",
    # Health
    5912: "Аптека",
    5975: "Аптека",
    5976: "Аптека",
    8011: "Медицина",
    8021: "Стоматология",
    8042: "Оптика",
    8043: "Оптика",
    8049: "Медицина",
    8050: "Медицина",
    8062: "Медицина",
    8071: "Медицина",
    8099: "Медицина",
    # Shopping
    5200: "Магазин",
    5300: "Опт",
    5310: "Магазин",
    5311: "Магазин",
    5331: "Магазин",
    5399: "Магазин",
    5611: "Одежда",
    5621: "Одежда",
    5631: "Одежда",
    5641: "Детская одежда",
    5651: "Одежда",
    5661: "Обувь",
    5691: "Одежда",
    5712: "Мебель",
    5722: "Бытовая техника",
    5732: "Электроника",
    5733: "Музыка",
    5734: "Софт",
    5735: "Электроника",
    5912: "Аптека",
    5942: "Книги",
    5944: "Ювелирка",
    5945: "Игрушки",
    5947: "Подарки",
    5948: "Кожгалантерея",
    5970: "Хобби",
    5995: "Зоомагазин",
    5999: "Магазин",
    # Entertainment / digital
    7311: "Реклама",
    7372: "IT/подписки",
    7392: "Консалтинг",
    7832: "Кино",
    7841: "Видеопрокат",
    7922: "Театр",
    7929: "Развлечения",
    7932: "Бильярд",
    7933: "Боулинг",
    7941: "Спорт",
    7991: "Музей",
    7992: "Гольф",
    7993: "Видеоигры",
    7994: "Видеоигры",
    7995: "Лотерея/казино",
    7996: "Парк аттракционов",
    7998: "Аквапарк",
    7999: "Развлечения",
    # Education
    8211: "Школа",
    8220: "Университет",
    8241: "Курсы",
    8244: "Курсы",
    8249: "Курсы",
    8299: "Образование",
    # Services
    7210: "Прачечная",
    7230: "Парикмахерская",
    7297: "СПА",
    7298: "СПА",
    7299: "Услуги",
    # Subscriptions / streaming common merchants are mostly 7372/4899
    4899: "Подписка",
}


def mcc_to_category(mcc_raw, *, is_income: bool = False, fallback_desc: str | None = None) -> str:
    """Map an MCC code to a human-readable category."""
    try:
        mcc = int(mcc_raw)
    except (TypeError, ValueError):
        return ("Доход" if is_income else "Покупка")
    if mcc in _MCC_CATEGORIES:
        return _MCC_CATEGORIES[mcc]
    # Income-side fallbacks.
    if is_income:
        if 4829 <= mcc <= 4900:
            return "Перевод"
        return "Доход"
    # Range-based fallbacks.
    if 4111 <= mcc <= 4789:
        return "Транспорт"
    if 5200 <= mcc <= 5999:
        return "Магазин"
    if 5811 <= mcc <= 5814:
        return "Кафе/ресторан"
    if 7800 <= mcc <= 7999:
        return "Развлечения"
    if 8000 <= mcc <= 8099:
        return "Медицина"
    if 8200 <= mcc <= 8299:
        return "Образование"
    return fallback_desc or "Покупка"


class MonobankService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.cipher = SecretCipher()

    def _pan_label(self, data: dict, ref: str) -> str | None:
        """
        Returns the card number in '4441...5985' format for the given account.
        Prefers maskedPan; falls back to last 4 digits of IBAN.
        Returns None if no card identifier is available — never falls back to
        the internal API account-id (which would look like 'VsMOl...sNg3').
        """
        for item in data.get("accounts", []):
            if str(item.get("id", "")) != ref:
                continue
            # 1. maskedPan — full card number with masking.
            pans = item.get("maskedPan", []) or []
            for raw_pan in pans:
                pan = str(raw_pan).replace(" ", "").replace("*", "X")
                digits = "".join(c for c in pan if c.isdigit())
                if len(digits) >= 8:
                    return f"{digits[:4]}...{digits[-4:]}"
            # 2. IBAN fallback — last 4 digits of account number.
            iban = item.get("iban")
            if iban:
                iban_digits = "".join(c for c in str(iban) if c.isdigit())
                if len(iban_digits) >= 8:
                    return f"{iban_digits[:4]}...{iban_digits[-4:]}"
            break
        return None

    async def link_token(self, db: AsyncSession, user_id: int, token: str, card_id: str) -> Account:
        data = await self._fetch_client_info(token)
        resolved_ref = self._resolve_ref_from_data(data, card_id) if data else (card_id or "").strip()
        label = (self._pan_label(data, resolved_ref) if data else None) or "Monobank card"
        account = (
            await db.execute(
                select(Account).where(
                    and_(
                        Account.user_id == user_id,
                        Account.account_type == AccountType.MONOBANK_CARD,
                        Account.external_ref == resolved_ref,
                    )
                )
            )
        ).scalars().first()
        if not account:
            account = Account(
                user_id=user_id,
                account_type=AccountType.MONOBANK_CARD,
                display_name=label,
                external_ref=resolved_ref,
            )
            db.add(account)
        else:
            account.display_name = label
        account.encrypted_secret = self.cipher.encrypt(token)
        account.is_active = True
        await db.commit()
        await db.refresh(account)
        return account

    async def _fetch_client_info(self, token: str) -> dict | None:
        url = f"{self.settings.monobank_api_url}/personal/client-info"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers={"X-Token": token})
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError):
            return None

    def _digits(self, value: str) -> str:
        return "".join(ch for ch in value if ch.isdigit())

    def _resolve_ref_from_data(self, data: dict, card_hint: str) -> str:
        hint = (card_hint or "").strip()
        accounts = data.get("accounts", [])
        if not accounts:
            return hint
        if not hint or hint.lower() in {"auto", "first"}:
            # Prefer accounts that have a masked PAN (real cards) over jars/eAid/etc.
            for item in accounts:
                if item.get("maskedPan"):
                    return str(item.get("id", hint))
            return str(accounts[0].get("id", hint))
        hint_digits = self._digits(hint)
        for item in accounts:
            acc_id = str(item.get("id", ""))
            if hint == acc_id:
                return acc_id
            pans = [str(x) for x in item.get("maskedPan", [])]
            if hint in pans:
                return acc_id
            if hint_digits:
                for pan in pans:
                    pan_digits = self._digits(pan)
                    if pan_digits.endswith(hint_digits) or hint_digits.endswith(pan_digits[-4:]):
                        return acc_id
        return hint

    async def _resolve_account_ref(self, token: str, card_hint: str) -> str:
        data = await self._fetch_client_info(token)
        if not data:
            return (card_hint or "").strip()
        return self._resolve_ref_from_data(data, card_hint)

    async def unlink(self, db: AsyncSession, user_id: int, card_id: str) -> None:
        account = (
            await db.execute(
                select(Account).where(
                    Account.user_id == user_id,
                    Account.account_type == AccountType.MONOBANK_CARD,
                    Account.external_ref == card_id,
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
                    Account.account_type == AccountType.MONOBANK_CARD,
                    Account.is_active.is_(True),
                )
            )
        ).scalars().first()

    async def sync_transactions(self, db: AsyncSession, account: Account) -> int:
        if not account.encrypted_secret:
            return 0
        token = self.cipher.decrypt(account.encrypted_secret)
        
        # If external_ref looks like a PAN (16 digits), try to resolve it to an internal ID first
        if account.external_ref and len(account.external_ref) == 16 and account.external_ref.isdigit():
            resolved = await self._resolve_account_ref(token, account.external_ref)
            if resolved != account.external_ref:
                account.external_ref = resolved
                await db.commit()

        # Monobank API allows max 31 days range. Let's take last 30 days.
        # Use a small offset for 'now' to avoid 400 if Monobank's clock is slightly behind.
        now_ts = int(datetime.utcnow().timestamp()) - 60
        from_ts = now_ts - (30 * 24 * 60 * 60)
        url = f"{self.settings.monobank_api_url}/personal/statement/{account.external_ref}/{from_ts}/{now_ts}"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(url, headers={"X-Token": token})
                if response.status_code == 429:
                    # Log but don't crash, just return 0 inserted
                    print(f"Monobank rate limit (429) for user {account.user_id}. Try again in 60s.")
                    return 0
                if response.status_code != 200:
                    print(f"Monobank error {response.status_code} for user {account.user_id}: {response.text}")
                    return 0
                response.raise_for_status()
                items = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"Monobank sync error for user {account.user_id}: {exc}")
            return 0

        inserted = 0
        # Check all items, don't limit to 10 here to ensure we don't miss anything
        # The user's requested limit is handled by the UI and API calls
        for item in items:
            if "id" not in item or "amount" not in item or "time" not in item:
                continue
            ext_id = str(item["id"])
            exists = (
                await db.execute(select(Transaction.id).where(Transaction.external_tx_id == ext_id))
            ).scalar_one_or_none()
            if exists:
                continue
            # Try to get a more descriptive name for the transaction
            description = item.get("description", "monobank")
            # In Monobank API 'description' usually contains the merchant or sender name
            
            amount = Decimal(str(abs(item["amount"]) / 100))
            is_income = item["amount"] > 0
            category = mcc_to_category(
                item.get("mcc"), is_income=is_income, fallback_desc=description,
            )
            db.add(
                Transaction(
                    user_id=account.user_id,
                    account_id=account.id,
                    tx_type=TransactionType.INCOME if is_income else TransactionType.EXPENSE,
                    amount=amount,
                    currency=Currency.UAH,
                    category=category,
                    description=description,
                    external_tx_id=ext_id,
                    created_at=datetime.utcfromtimestamp(item["time"]),
                )
            )
            inserted += 1
        await db.commit()
        return inserted

    async def get_live_balance(
        self, account: Account
    ) -> tuple[Decimal, Currency, str | None] | None:
        """Returns (balance, currency, masked_pan_label) or None on failure."""
        if not account.encrypted_secret:
            return None
        token = self.cipher.decrypt(account.encrypted_secret)
        data = await self._fetch_client_info(token)
        if not data:
            return None

        accounts = data.get("accounts", [])
        for item in accounts:
            acc_id = str(item.get("id", ""))
            pan_list = [str(x) for x in item.get("maskedPan", [])]
            if account.external_ref and (account.external_ref == acc_id or account.external_ref in pan_list):
                amount_minor = Decimal(str(item.get("balance", 0)))
                currency_code = int(item.get("currencyCode", 980))
                currency = Currency.USD if currency_code == 840 else Currency.UAH
                label = self._pan_label(data, acc_id)
                return amount_minor / Decimal("100"), currency, label
        # Fallback: if linked ref wasn't found, use first account.
        if accounts:
            first = accounts[0]
            amount_minor = Decimal(str(first.get("balance", 0)))
            currency_code = int(first.get("currencyCode", 980))
            currency = Currency.USD if currency_code == 840 else Currency.UAH
            label = self._pan_label(data, str(first.get("id", "")))
            return amount_minor / Decimal("100"), currency, label
        return None
