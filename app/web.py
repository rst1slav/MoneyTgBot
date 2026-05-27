"""
Веб-сервис для хостинга карточек, которые Telegram подгружает как link-preview.

Логика:
  POST /upload (multipart image=<png>, title=..., description=...)
        → сохраняет PNG на диск, возвращает {id, page_url, image_url}
  GET  /c/{id}       → HTML-страница с og:image (Telegram парсит для превью)
  GET  /i/{id}.png   → сама картинка
  GET  /health       → диагностика

Хранилище — папка на диске (по умолчанию /var/lib/moneybot-cards).
Карточки переживают рестарт сервиса.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse


BASE_URL = os.getenv("CARD_BASE_URL", "https://imgyonagen.org").rstrip("/")
STORAGE_DIR = Path(os.getenv("CARD_STORAGE_DIR", "/var/lib/moneybot-cards"))
UPLOAD_TOKEN = os.getenv("CARD_UPLOAD_TOKEN", "").strip()  # если задан — требуем заголовок X-Upload-Token

STORAGE_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="MoneyBot Card Hosting")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id() -> str:
    """URL-безопасный 12-символьный id из латиницы+цифр."""
    # token_urlsafe(9) даёт 12 символов после base64 → берём первые 12 и нормализуем
    return secrets.token_urlsafe(9)[:12].replace("-", "x").replace("_", "y").lower()


def _meta_path(card_id: str) -> Path:
    return STORAGE_DIR / f"{card_id}.meta"


def _png_path(card_id: str) -> Path:
    return STORAGE_DIR / f"{card_id}.png"


def _save_meta(card_id: str, title: str, description: str) -> None:
    # Простой формат: первая строка — title, вторая — description
    _meta_path(card_id).write_text(
        f"{title}\n{description}\n",
        encoding="utf-8",
    )


def _load_meta(card_id: str) -> tuple[str, str] | None:
    path = _meta_path(card_id)
    if not path.exists():
        return None
    parts = path.read_text(encoding="utf-8").split("\n")
    title = parts[0] if parts else ""
    description = parts[1] if len(parts) > 1 else ""
    return title, description


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
         .replace("'", "&#39;")
    )


def _check_token(token_header: str | None) -> None:
    if UPLOAD_TOKEN and token_header != UPLOAD_TOKEN:
        raise HTTPException(401, "invalid upload token")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/upload")
async def upload_image(
    image: UploadFile = File(...),
    title: str = Form("Money Bot"),
    description: str = Form(""),
    x_upload_token: str | None = None,
) -> dict:
    # FastAPI парсит заголовки через Header(...), но проще — взять из starlette напрямую
    # Здесь токен передаём только через Form, чтобы не усложнять (можно не задавать вообще)
    _check_token(x_upload_token)

    if image.content_type not in ("image/png", "image/jpeg"):
        raise HTTPException(415, "expected image/png or image/jpeg")

    data = await image.read()
    if not data:
        raise HTTPException(400, "empty image")
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "image too large (max 5MB)")

    card_id = _new_id()
    # На всякий случай — если коллизия, генерим заново
    while _png_path(card_id).exists():
        card_id = _new_id()

    _png_path(card_id).write_bytes(data)
    _save_meta(card_id, title, description)

    return {
        "id": card_id,
        "page_url": f"{BASE_URL}/c/{card_id}",
        "image_url": f"{BASE_URL}/i/{card_id}.png",
    }


@app.get("/c/{card_id}", response_class=HTMLResponse)
async def card_page(card_id: str) -> str:
    meta = _load_meta(card_id)
    if meta is None or not _png_path(card_id).exists():
        raise HTTPException(404, "card not found")
    img_url = f"{BASE_URL}/i/{card_id}.png"
    # Минимально возможный HTML: только og:image и og:type=website.
    # Без og:title/description/site_name Telegram рендерит превью как чистую
    # медиа-карточку (как у CryptoBot), без хедера и заголовков.
    # Заголовок <title> пустой — иначе Telegram возьмёт его как название превью.
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title> </title>
<meta property="og:type" content="website">
<meta property="og:image" content="{img_url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="{img_url}">
</head><body style="margin:0;background:#000;">
<img src="{img_url}" style="display:block;width:100%;height:auto;">
</body></html>"""


@app.get("/i/{filename}")
async def card_image(filename: str) -> Response:
    # Принимаем только {id}.png — никаких path-traversal
    if not filename.endswith(".png"):
        raise HTTPException(404)
    card_id = filename[:-4]
    if not card_id.isalnum() or len(card_id) > 32:
        raise HTTPException(404)
    path = _png_path(card_id)
    if not path.exists():
        raise HTTPException(404)
    return Response(
        content=path.read_bytes(),
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@app.get("/health")
async def health() -> dict:
    pngs = list(STORAGE_DIR.glob("*.png"))
    return {
        "status": "ok",
        "storage_dir": str(STORAGE_DIR),
        "cards_on_disk": len(pngs),
    }


@app.get("/_debug/keys")
async def debug_keys() -> dict:
    pngs = sorted(STORAGE_DIR.glob("*.png"))
    return {"count": len(pngs), "ids": [p.stem for p in pngs[:50]]}
