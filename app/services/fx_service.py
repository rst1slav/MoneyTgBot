from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Currency, FxRate


class FxService:
    def __init__(self) -> None:
        self.settings = get_settings()

    async def refresh_uah_usd(self, db: AsyncSession) -> Decimal:
        rate = await self._fetch_uah_per_usd_from_monobank()
        if rate is None:
            raise RuntimeError("Cannot fetch USD/UAH rate from Monobank")

        db.add(
            FxRate(
                base_currency=Currency.USD,
                quote_currency=Currency.UAH,
                rate=rate,
                rate_at=datetime.utcnow(),
            )
        )
        await db.commit()
        return rate

    async def _fetch_uah_per_usd_from_monobank(self) -> Decimal | None:
        # Monobank public currency endpoint, USD=840, UAH=980.
        url = f"{self.settings.monobank_api_url}/bank/currency"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
                response.raise_for_status()
                pairs = response.json()
        except (httpx.HTTPError, ValueError):
            return None

        for item in pairs:
            if item.get("currencyCodeA") == 840 and item.get("currencyCodeB") == 980:
                rate_buy = item.get("rateBuy")
                rate_sell = item.get("rateSell")
                rate_cross = item.get("rateCross")
                if rate_buy and rate_sell:
                    return (Decimal(str(rate_buy)) + Decimal(str(rate_sell))) / Decimal("2")
                if rate_cross:
                    return Decimal(str(rate_cross))
        return None

    async def latest_uah_per_usd(self, db: AsyncSession) -> Decimal:
        stmt = (
            select(FxRate)
            .where(FxRate.base_currency == Currency.USD, FxRate.quote_currency == Currency.UAH)
            .order_by(desc(FxRate.rate_at))
            .limit(1)
        )
        rate = (await db.execute(stmt)).scalars().first()
        if rate:
            return Decimal(rate.rate)
        fetched = await self._fetch_uah_per_usd_from_monobank()
        if fetched is not None:
            return fetched
        return Decimal("40.00")

    async def convert_to_uah(self, db: AsyncSession, amount: Decimal, currency: Currency) -> Decimal:
        if currency == Currency.UAH:
            return amount
        return amount * await self.latest_uah_per_usd(db)
