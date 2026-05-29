from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Currency(str, enum.Enum):
    UAH = "UAH"
    USD = "USD"
    TON = "TON"
    RUB = "RUB"
    EUR = "EUR"
    BYN = "BYN"
    PLN = "PLN"
    UZS = "UZS"
    USDT = "USDT"


class TransactionType(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"


class AccountType(str, enum.Enum):
    MANUAL = "manual"
    MONOBANK_CARD = "monobank_card"
    TON_WALLET = "ton_wallet"
    TELEGRAM_GIFTS = "telegram_gifts"
    PRIVATBANK_CARD = "privatbank_card"
    OSCHADBANK_CARD = "oschadbank_card"
    SENSEBANK_CARD = "sensebank_card"
    PUMB_CARD = "pumb_card"
    RAIFFEISEN_CARD = "raiffeisen_card"
    UKRSIB_CARD = "ukrsib_card"
    TBANK_CARD = "tbank_card"
    ALFABANK_CARD = "alfabank_card"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Kyiv")
    base_currency: Mapped[Currency] = mapped_column(Enum(Currency), default=Currency.UAH)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    fee_payment_mode: Mapped[str] = mapped_column(String(16), default="same")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    accounts: Mapped[list[Account]] = relationship(back_populates="user")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_type: Mapped[AccountType] = mapped_column(Enum(AccountType), index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    external_ref: Mapped[str | None] = mapped_column(String(255), index=True)
    encrypted_secret: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    is_favorite: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="accounts")
    transactions: Mapped[list[Transaction]] = relationship(back_populates="account")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    tx_type: Mapped[TransactionType] = mapped_column(Enum(TransactionType), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[Currency] = mapped_column(Enum(Currency), index=True)
    category: Mapped[str] = mapped_column(String(128), default="other")
    description: Mapped[str] = mapped_column(String(512), default="")
    external_tx_id: Mapped[str | None] = mapped_column(String(255), index=True)
    notified: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    account: Mapped[Account] = relationship(back_populates="transactions")


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[Currency] = mapped_column(Enum(Currency))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class FxRate(Base):
    __tablename__ = "fx_rates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    base_currency: Mapped[Currency] = mapped_column(Enum(Currency))
    quote_currency: Mapped[Currency] = mapped_column(Enum(Currency))
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    rate_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class DailyRateSnapshot(Base):
    """Daily USD-rate snapshot per currency, used for period-over-period % deltas."""
    __tablename__ = "daily_rate_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ccy_code: Mapped[str] = mapped_column(String(8), index=True)
    usd_rate: Mapped[Decimal] = mapped_column(Numeric(20, 10))
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProfileSnapshot(Base):
    """Daily aggregate snapshot used to compute period-over-period deltas in profile."""
    __tablename__ = "profile_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    total_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    total_uah: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    mono_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    ton_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    gifts_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))


class GiftItem(Base):
    """Per-user persisted gift entry: the slug owned + last computed price.
    Survives bot restarts so the profile never renders empty after a redeploy.
    """
    __tablename__ = "gift_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    slug: Mapped[str] = mapped_column(String(128), index=True)
    price_ton: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    priced_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BasicGiftItem(Base):
    """User-managed list of non-upgraded gifts with fixed USD prices."""
    __tablename__ = "basic_gift_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    gift_id: Mapped[str] = mapped_column(String(64), index=True)
    gift_name: Mapped[str] = mapped_column(String(255))
    price_usd: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    period: Mapped[str] = mapped_column(String(16), index=True)
    file_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
