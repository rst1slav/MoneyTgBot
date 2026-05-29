from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    AccountType,
    Currency,
    Transaction,
    TransactionType,
    User,
)
from app.services.manual_input_parser import parse_manual_input


class LedgerService:
    async def ensure_user(self, db: AsyncSession, telegram_id: int, username: str | None) -> User:
        user = (
            await db.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalars().first()
        if user:
            return user

        user = User(telegram_id=telegram_id, username=username, base_currency=Currency.UAH)
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    async def ensure_manual_account(self, db: AsyncSession, user_id: int) -> Account:
        account = (
            await db.execute(
                select(Account).where(and_(Account.user_id == user_id, Account.account_type == AccountType.MANUAL))
            )
        ).scalars().first()
        if account:
            return account

        account = Account(user_id=user_id, account_type=AccountType.MANUAL, display_name="Manual account")
        db.add(account)
        await db.commit()
        await db.refresh(account)
        return account

    async def add_manual_transaction(self, db: AsyncSession, user_id: int, text: str) -> Transaction:
        parsed = parse_manual_input(text)
        account = await self.ensure_manual_account(db, user_id)
        tx = Transaction(
            user_id=user_id,
            account_id=account.id,
            tx_type=parsed.tx_type,
            amount=parsed.amount,
            currency=parsed.currency,
            category=parsed.category,
            description=parsed.description,
            created_at=datetime.utcnow(),
        )
        db.add(tx)
        await db.commit()
        await db.refresh(tx)
        return tx

    async def balance_by_currency(
        self,
        db: AsyncSession,
        user_id: int,
        account_types: list[AccountType] | None = None,
    ) -> dict[Currency, Decimal]:
        return await self.balance_by_currency_for_account_types(
            db,
            user_id,
            account_types or [AccountType.MANUAL, AccountType.MONOBANK_CARD, AccountType.TON_WALLET],
        )

    async def balance_by_currency_for_account_types(
        self,
        db: AsyncSession,
        user_id: int,
        account_types: list[AccountType],
    ) -> dict[Currency, Decimal]:
        expense_case = func.sum(case((Transaction.tx_type == TransactionType.EXPENSE, Transaction.amount), else_=0))
        income_case = func.sum(case((Transaction.tx_type == TransactionType.INCOME, Transaction.amount), else_=0))
        stmt = (
            select(Transaction.currency, income_case.label("income"), expense_case.label("expense"))
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.user_id == user_id,
                Account.account_type.in_(account_types),
            )
            .group_by(Transaction.currency)
        )
        rows = (await db.execute(stmt)).all()
        result: dict[Currency, Decimal] = {}
        for currency, income, expense in rows:
            result[currency] = Decimal(income or 0) - Decimal(expense or 0)
        return result

    async def get_active_accounts_by_type(
        self,
        db: AsyncSession,
        user_id: int,
        account_type: AccountType,
    ) -> list[Account]:
        rows = await db.execute(
            select(Account).where(
                and_(
                    Account.user_id == user_id,
                    Account.account_type == account_type,
                    Account.is_active.is_(True),
                )
            ).order_by(Account.sort_order, Account.id)
        )
        return list(rows.scalars().all())

    async def get_account_by_id(
        self,
        db: AsyncSession,
        user_id: int,
        account_id: int,
    ) -> Account | None:
        return (
            await db.execute(
                select(Account).where(
                    and_(Account.id == account_id, Account.user_id == user_id)
                )
            )
        ).scalars().first()

    async def total_income_by_currency(
        self,
        db: AsyncSession,
        user_id: int,
        account_types: list[AccountType] | None = None,
    ) -> dict[Currency, Decimal]:
        stmt = (
            select(Transaction.currency, func.sum(Transaction.amount).label("total_income"))
            .join(Account, Account.id == Transaction.account_id)
            .where(
                Transaction.user_id == user_id,
                Transaction.tx_type == TransactionType.INCOME,
            )
        )
        if account_types:
            stmt = stmt.where(Account.account_type.in_(account_types))
        
        stmt = stmt.group_by(Transaction.currency)
        rows = (await db.execute(stmt)).all()
        result: dict[Currency, Decimal] = {}
        for currency, total_income in rows:
            result[currency] = Decimal(total_income or 0)
        return result
