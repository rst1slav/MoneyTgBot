from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, AccountType, Currency, Transaction, TransactionType
from app.services.fx_service import FxService
from app.services.ton_service import TonService


# USD-equivalent thresholds used by the size filter.
_SMALL_USD = Decimal("10")
_BIG_USD = Decimal("100")
# Hard cap on rows scanned when size filter is active (USD conversion happens in Python).
_SIZE_FILTER_FETCH_LIMIT = 500


@dataclass
class HistoryFilters:
    tx_type: TransactionType | None = None
    min_amount: Decimal | None = None  # legacy, in transaction's native currency
    max_amount: Decimal | None = None
    size: str | None = None            # "small" / "big" — interpreted in USD
    account_type: AccountType | None = None
    account_types: list[AccountType] | None = None  # e.g. all UA cards
    account_id: int | None = None
    category: str | None = None


class HistoryService:
    def __init__(self) -> None:
        self.fx = FxService()
        self.ton = TonService()

    @staticmethod
    def _convert_to_usd(
        amount: Decimal,
        currency: Currency,
        uah_per_usd: Decimal,
        ton_usd: Decimal,
    ) -> Decimal:
        if currency == Currency.USD:
            return amount
        if currency == Currency.UAH:
            return amount / uah_per_usd if uah_per_usd > 0 else Decimal("0")
        if currency == Currency.TON:
            return amount * ton_usd
        # Unknown currencies — treat 1:1 to USD so the filter doesn't silently
        # drop everything.
        return amount

    async def get_transactions(
        self,
        db: AsyncSession,
        user_id: int,
        filters: HistoryFilters,
        page: int = 1,
        per_page: int = 20,
    ) -> list[Transaction]:
        stmt: Select[tuple[Transaction]] = select(Transaction).join(Account).where(Transaction.user_id == user_id)
        where_clauses = []
        if filters.tx_type:
            where_clauses.append(Transaction.tx_type == filters.tx_type)
        if filters.min_amount is not None:
            where_clauses.append(Transaction.amount >= filters.min_amount)
        if filters.max_amount is not None:
            where_clauses.append(Transaction.amount <= filters.max_amount)
        if filters.account_type:
            where_clauses.append(Account.account_type == filters.account_type)
        if filters.account_types:
            where_clauses.append(Account.account_type.in_(filters.account_types))
        if filters.account_id is not None:
            where_clauses.append(Transaction.account_id == filters.account_id)
        if filters.category:
            where_clauses.append(Transaction.category == filters.category.lower())
        if where_clauses:
            stmt = stmt.where(and_(*where_clauses))
        stmt = stmt.order_by(Transaction.created_at.desc())

        # Size filter operates in USD across all currencies; we have to compute
        # USD-equivalent per row, so paginate in Python after filtering.
        if filters.size in {"small", "big"}:
            uah_per_usd = await self.fx.latest_uah_per_usd(db) or Decimal("40")
            ton_usd = await self.ton.ton_price_usd() or Decimal("3")
            rows = list((await db.execute(stmt.limit(_SIZE_FILTER_FETCH_LIMIT))).scalars().all())
            filtered: list[Transaction] = []
            for tx in rows:
                usd = self._convert_to_usd(
                    Decimal(tx.amount), tx.currency, uah_per_usd, ton_usd
                )
                if filters.size == "small" and usd < _SMALL_USD:
                    filtered.append(tx)
                elif filters.size == "big" and usd > _BIG_USD:
                    filtered.append(tx)
            offset = (page - 1) * per_page
            return filtered[offset : offset + per_page]

        stmt = stmt.offset((page - 1) * per_page).limit(per_page)
        return list((await db.execute(stmt)).scalars().all())

    async def count_transactions(
        self,
        db: AsyncSession,
        user_id: int,
        filters: HistoryFilters,
    ) -> int:
        """
        Cheap total-rows count for the same filter set. For size filter this is
        approximate (it falls back to the rows-scanned cap), since USD conversion
        happens in Python.
        """
        stmt = select(func.count(Transaction.id)).join(Account).where(Transaction.user_id == user_id)
        where_clauses = []
        if filters.tx_type:
            where_clauses.append(Transaction.tx_type == filters.tx_type)
        if filters.min_amount is not None:
            where_clauses.append(Transaction.amount >= filters.min_amount)
        if filters.max_amount is not None:
            where_clauses.append(Transaction.amount <= filters.max_amount)
        if filters.account_type:
            where_clauses.append(Account.account_type == filters.account_type)
        if filters.account_types:
            where_clauses.append(Account.account_type.in_(filters.account_types))
        if filters.account_id is not None:
            where_clauses.append(Transaction.account_id == filters.account_id)
        if filters.category:
            where_clauses.append(Transaction.category == filters.category.lower())
        if where_clauses:
            stmt = stmt.where(and_(*where_clauses))

        if filters.size in {"small", "big"}:
            # Approximation: cap matches the get_transactions scan window.
            return min(
                (await db.execute(stmt)).scalar() or 0,
                _SIZE_FILTER_FETCH_LIMIT,
            )
        return int((await db.execute(stmt)).scalar() or 0)

    async def weekly_inout_stats_usd(
        self, db: AsyncSession, user_id: int,
    ) -> tuple[Decimal, Decimal]:
        """
        Возвращает (deposits_usd, withdrawals_usd) — сумма поступлений и
        списаний за последние 7 дней, конвертированные в USD.
        Используется для диаграммы в истории.
        """
        since = datetime.utcnow() - timedelta(days=7)
        rows = list((await db.execute(
            select(Transaction).where(
                and_(
                    Transaction.user_id == user_id,
                    Transaction.created_at >= since,
                )
            )
        )).scalars().all())
        uah_per_usd = await self.fx.latest_uah_per_usd(db) or Decimal("40")
        ton_usd = await self.ton.ton_price_usd() or Decimal("3")
        dep = Decimal("0")
        wd = Decimal("0")
        for tx in rows:
            usd = self._convert_to_usd(
                Decimal(tx.amount), tx.currency, uah_per_usd, ton_usd,
            )
            if tx.tx_type == TransactionType.INCOME:
                dep += usd
            else:
                wd += usd
        return dep, wd
