"""
Pie-chart карточка для раздела «История»:
зелёный сектор — депозиты, красный — выводы.

Дизайн: тёмный фон + Yona-watermark + белая карточка с круговой диаграммой
по центру. Размеры — 1600×1000 (как rate-card).

Карточка генерится один раз в сутки на юзера, заливается на наш веб-сервис,
URL кэшируется в памяти.
"""

from __future__ import annotations

import io
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import math

from PIL import Image, ImageDraw, ImageFilter

# Используем те же примитивы, что и rate-карточка (фон, watermark, шрифты).
from app.services.card_service import (
    _vertical_gradient,
    _watermark_layer,
    _font,
    _RATE_DARK_TOP,
    _RATE_DARK_BOT,
    _RATE_CARD_X,
    _RATE_CARD_Y,
    _RATE_CARD_W,
    _RATE_CARD_H,
    _RATE_CARD_R,
    _RATE_W,
    _RATE_H,
)


# Палитра — как у CMC: насыщенный зелёный и красный.
_PIE_GREEN = (35, 180, 110)         # #23B46E — депозиты
_PIE_RED = (234, 57, 67)            # #EA3943 — выводы


def render_inout_pie(deposits_usd: Decimal, withdrawals_usd: Decimal) -> bytes:
    """
    Рисует pie-карточку deposits vs withdrawals.
    Если оба = 0 — рисуется серый круг с надписью «нет данных».
    """
    SS = 2
    s = lambda v: int(round(v * SS))    # noqa: E731

    W, H = _RATE_W * SS, _RATE_H * SS

    # 1. Тёмный фон + watermark
    bg = _vertical_gradient(W, H, _RATE_DARK_TOP, _RATE_DARK_BOT).convert("RGBA")
    bg.alpha_composite(_watermark_layer(W, H))

    # 2. Тень
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow_layer).rounded_rectangle(
        (s(_RATE_CARD_X), s(_RATE_CARD_Y + 4),
         s(_RATE_CARD_X + _RATE_CARD_W), s(_RATE_CARD_Y + _RATE_CARD_H + 4)),
        radius=s(_RATE_CARD_R),
        fill=(0, 0, 0, int(0.10 * 255)),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(12.5 * SS))
    bg.alpha_composite(shadow_layer)

    # 3. Белая карточка
    card_rect = (
        s(_RATE_CARD_X), s(_RATE_CARD_Y),
        s(_RATE_CARD_X + _RATE_CARD_W) - 1, s(_RATE_CARD_Y + _RATE_CARD_H) - 1,
    )
    ImageDraw.Draw(bg).rounded_rectangle(
        card_rect, radius=s(_RATE_CARD_R), fill=(255, 255, 255, 255),
    )
    img = bg

    # 4. Пирог по центру карточки
    total = float(deposits_usd) + float(withdrawals_usd)
    cx = (_RATE_CARD_X + _RATE_CARD_W / 2)
    cy = (_RATE_CARD_Y + _RATE_CARD_H / 2)
    radius = 300   # логический радиус

    bbox = (
        s(cx - radius), s(cy - radius),
        s(cx + radius), s(cy + radius),
    )
    drw = ImageDraw.Draw(img)

    if total <= 0:
        # Серый круг + «нет данных»
        drw.ellipse(bbox, fill=(220, 220, 220, 255))
        return _finalize(img, SS)

    dep_pct = float(deposits_usd) / total
    wd_pct = float(withdrawals_usd) / total

    # Pillow pieslice: 0° = вправо. Начинаем с -90° (верх) и идём по часовой стрелке.
    start = -90.0
    dep_angle = dep_pct * 360
    wd_angle = wd_pct * 360

    if deposits_usd > 0:
        drw.pieslice(bbox, start, start + dep_angle, fill=_PIE_GREEN)
    if withdrawals_usd > 0:
        drw.pieslice(bbox, start + dep_angle, start + dep_angle + wd_angle, fill=_PIE_RED)

    # Тонкий белый зазор между секторами — две линии радиус→центр.
    if deposits_usd > 0 and withdrawals_usd > 0:
        gap_w = s(8)
        # граница начала депозитов (start angle = -90)
        a1 = math.radians(start)
        # граница конца депозитов / начала выводов
        a2 = math.radians(start + dep_angle)
        for a in (a1, a2):
            ex = s(cx + radius * math.cos(a))
            ey = s(cy + radius * math.sin(a))
            drw.line(
                [(s(cx), s(cy)), (ex, ey)],
                fill=(255, 255, 255, 255), width=gap_w,
            )

    # Подписи процентов внутри секторов (если хотя бы 8%, иначе тесно).
    pct_font = _font(s(50), weight="bold")
    label_radius = radius * 0.55

    def _draw_pct_label(sector_start: float, sector_end: float, pct: float, color_bg: tuple):
        mid = math.radians((sector_start + sector_end) / 2)
        lx = cx + label_radius * math.cos(mid)
        ly = cy + label_radius * math.sin(mid)
        txt = f"{pct * 100:.1f}%"
        drw.text((s(lx), s(ly)), txt, font=pct_font, fill=(255, 255, 255, 255),
                 anchor="mm")

    if dep_pct >= 0.08:
        _draw_pct_label(start, start + dep_angle, dep_pct, _PIE_GREEN)
    if wd_pct >= 0.08:
        _draw_pct_label(start + dep_angle, start + dep_angle + wd_angle, wd_pct, _PIE_RED)

    return _finalize(img, SS)


def _finalize(img: Image.Image, SS: int) -> bytes:
    if SS != 1:
        img = img.resize((_RATE_W, _RATE_H), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_inout_pie_to_disk(
    deposits_usd: Decimal, withdrawals_usd: Decimal, user_id: int,
) -> Path:
    """
    Кэш на диске, ключом — день + округлённые суммы. Чтобы карточка не плодилась.
    """
    today = date.today().isoformat()
    key = f"inout_pie_{user_id}_{today}_{float(deposits_usd):.0f}_{float(withdrawals_usd):.0f}"
    path = Path("cards") / f"{key}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(render_inout_pie(deposits_usd, withdrawals_usd))
    return path
