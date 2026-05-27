"""
Веб-сервис для генерации карточек-превью в Telegram.

Логика:
  /card/{card_id}     → HTML с og:image, который Telegram парсит для превью
  /card/{card_id}.png → сама картинка (рендерится из HTML через Playwright)

Данные карточки временно хранятся в памяти. Позже заменим на БД.
"""

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from playwright.async_api import async_playwright
from pydantic import BaseModel
from typing import Dict
import uuid
import os

BASE_URL = os.getenv("CARD_BASE_URL", "https://imgyonagen.org")

app = FastAPI(title="MoneyBot Card Preview")

_cards: Dict[str, dict] = {}


class CardData(BaseModel):
    title: str
    amount: str
    currency: str
    subtitle: str | None = None


@app.post("/card")
async def create_card(data: CardData) -> dict:
    card_id = uuid.uuid4().hex[:12]
    _cards[card_id] = data.model_dump()
    return {
        "id": card_id,
        "url": f"{BASE_URL}/card/{card_id}",
        "image_url": f"{BASE_URL}/img/{card_id}.png",
    }


@app.get("/card/{card_id}", response_class=HTMLResponse)
async def card_page(card_id: str) -> str:
    if card_id not in _cards:
        raise HTTPException(404, "card not found")
    data = _cards[card_id]
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{data['title']}</title>
<meta property="og:title" content="{data['title']}">
<meta property="og:description" content="{data.get('subtitle') or ''}">
<meta property="og:image" content="{BASE_URL}/img/{card_id}.png">
<meta property="og:image:width" content="600">
<meta property="og:image:height" content="400">
<meta property="og:type" content="website">
</head><body>
<h1>{data['title']}</h1>
<p>{data['amount']} {data['currency']}</p>
</body></html>"""


@app.get("/img/{card_id}.png")
async def card_png(card_id: str) -> Response:
    if card_id not in _cards:
        raise HTTPException(404, "card not found")
    data = _cards[card_id]
    html = _render_card_html(data)

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 600, "height": 400})
        await page.set_content(html, wait_until="networkidle")
        png = await page.screenshot(type="png", omit_background=False)
        await browser.close()

    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "cards_in_memory": len(_cards)}


@app.get("/_debug/keys")
async def debug_keys() -> dict:
    return {"count": len(_cards), "keys": list(_cards.keys())}


def _render_card_html(data: dict) -> str:
    title = data["title"]
    amount = data["amount"]
    currency = data["currency"]
    subtitle = data.get("subtitle") or ""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 600px; height: 400px;
    font-family: -apple-system, "SF Pro Display", "Segoe UI", Roboto, sans-serif;
    background: linear-gradient(135deg, #1e3a5f 0%, #2d5a8c 100%);
    color: #fff;
    display: flex; flex-direction: column;
    padding: 40px;
  }}
  .title {{ font-size: 22px; opacity: 0.85; margin-bottom: 8px; }}
  .subtitle {{ font-size: 16px; opacity: 0.6; margin-bottom: 40px; }}
  .amount {{ font-size: 84px; font-weight: 700; line-height: 1; }}
  .currency {{ font-size: 36px; opacity: 0.8; margin-top: 12px; }}
  .footer {{ margin-top: auto; font-size: 14px; opacity: 0.5; }}
</style></head>
<body>
  <div class="title">{title}</div>
  <div class="subtitle">{subtitle}</div>
  <div class="amount">{amount}</div>
  <div class="currency">{currency}</div>
  <div class="footer">via @YourBot</div>
</body></html>"""
