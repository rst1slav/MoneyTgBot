from fastapi import FastAPI, Query
from fastapi.responses import Response

from app.services.card_service import (
    RateCard,
    ReceivedCard,
    render_rate_card,
    render_received_card,
)

app = FastAPI(title="Money Telegram Bot API")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/cards/received.png", responses={200: {"content": {"image/png": {}}}})
async def card_received(
    amount: str = Query(..., description="formatted amount string, e.g. 8.009821"),
    currency: str = Query("USDT", description="ticker — USDT, TON, USD, ..."),
    usd: str = Query("", description="optional already-formatted USD label, e.g. '$ 8'"),
) -> Response:
    """
    Returns a dynamically rendered PNG card. Designed to be referenced directly
    from inside a Telegram message ([send_photo](photo=URL)) so Telegram fetches
    and caches it.

    Example: /cards/received.png?amount=8.009821&currency=USDT&usd=%248
    """
    usd_label = usd or "$ ?"
    png = render_received_card(
        ReceivedCard(amount=amount, currency=currency.upper(), usd_label=usd_label)
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/cards/rate.png", responses={200: {"content": {"image/png": {}}}})
async def card_rate(
    base: str = Query("TON", description="base ticker, e.g. TON"),
    quote: str = Query("USD", description="quote ticker, e.g. USD"),
    price: float = Query(..., description="current price in quote ccy"),
    change: float = Query(0.0, description="signed % change over the window"),
    prices: str = Query("", description="comma-separated price history old→new"),
    dates: str = Query("", description="comma-separated date labels, e.g. 'MAY 4,MAY 6,MAY 8,MAY 10'"),
) -> Response:
    """
    Returns a TON/USD-style chart card.

    Example:
      /cards/rate.png?base=TON&quote=USD&price=2.49&change=87.48
        &prices=2.12,2.14,2.15,2.20,2.30,2.45,2.60,2.78,2.90,2.97,2.85,2.80
        &dates=MAY%204,MAY%206,MAY%208,MAY%2010
    """
    history = [float(p) for p in prices.split(",") if p.strip()]
    date_labels = [d.strip() for d in dates.split(",") if d.strip()]
    png = render_rate_card(
        RateCard(
            base=base,
            quote=quote,
            price=price,
            change_pct=change,
            history=history,
            date_labels=date_labels,
        )
    )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )
