from app.services.history_service import HistoryFilters


def test_history_filters_defaults() -> None:
    filters = HistoryFilters()
    assert filters.tx_type is None
    assert filters.min_amount is None
    assert filters.account_type is None
    assert filters.category is None
