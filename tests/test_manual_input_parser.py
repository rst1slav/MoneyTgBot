from decimal import Decimal

from app.db.models import Currency, TransactionType
from app.services.manual_input_parser import parse_manual_input


def test_parse_expense_uah() -> None:
    item = parse_manual_input("-100 грн магазин конфеты")
    assert item.amount == Decimal("100")
    assert item.currency == Currency.UAH
    assert item.tx_type == TransactionType.EXPENSE
    assert item.category == "магазин"


def test_parse_income_usd() -> None:
    item = parse_manual_input("+50 usd salary freelance")
    assert item.amount == Decimal("50")
    assert item.currency == Currency.USD
    assert item.tx_type == TransactionType.INCOME
