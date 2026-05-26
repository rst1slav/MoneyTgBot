import re
from dataclasses import dataclass
from decimal import Decimal

from app.db.models import Currency, TransactionType


@dataclass
class ParsedManualInput:
    amount: Decimal
    currency: Currency
    category: str
    description: str
    tx_type: TransactionType


def parse_manual_input(text: str) -> ParsedManualInput:
    """
    Expected format:
    `100 грн магазин конфеты`
    `+50 usd salary freelance`
    """
    pattern = re.compile(r"^\s*([+-]?\d+(?:[.,]\d{1,2})?)\s*(грн|uah|usd|\$)\s+(\S+)\s*(.*)$", re.I)
    match = pattern.match(text.strip())
    if not match:
        raise ValueError("Invalid format. Use: <amount> <currency> <category> <description>")

    raw_amount, raw_currency, category, description = match.groups()
    amount = Decimal(raw_amount.replace(",", "."))
    currency = Currency.USD if raw_currency.lower() in {"usd", "$"} else Currency.UAH
    tx_type = TransactionType.INCOME if amount >= 0 else TransactionType.EXPENSE

    return ParsedManualInput(
        amount=abs(amount),
        currency=currency,
        category=category.lower(),
        description=description.strip(),
        tx_type=tx_type,
    )
