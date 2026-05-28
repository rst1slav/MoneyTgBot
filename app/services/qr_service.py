"""
QR-генератор для адресов кошельков.

Генерит PNG с:
  - модулями со скруглёнными краями (стиль как у Telegram Wallet)
  - белым фоном
  - центральной круглой плашкой с иконкой ракеты (под цвет бренда)
  - небольшими внешними скруглёнными уголками самой картинки

Используется в экране пополнения — карточка отправляется как link preview.
"""

from __future__ import annotations

import io
from pathlib import Path

import qrcode
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers.pil import RoundedModuleDrawer
from PIL import Image, ImageDraw, ImageFilter


# Брендовый цвет TON — синий, как на референсе.
QR_FG_COLOR = (0, 152, 234)        # #0098EA
QR_BG_COLOR = (255, 255, 255)


def _rounded_corner_mask(size: tuple[int, int], radius: int) -> Image.Image:
    """Чёрно-белая маска — белые внутри прямоугольника со скруглёнными углами."""
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size[0] - 1, size[1] - 1), radius=radius, fill=255,
    )
    return mask


# Yona-logo SVG paths из Logo.svg (viewBox 1032x1242).
# Большая молниеподобная фигура + маленький треугольник (вспышка).
_YONA_BOLT: list[tuple[float, float]] = [
    (-0.000157246, 407.685),
    (245.972, 935.174),
    (280.439, 1241.57),
    (503.792, 1029.01),
    (1031.28, 783.041),
    (826.305, 343.466),
    (515.641, 595.363),
    (439.574, 202.708),
]
_YONA_TRI: list[tuple[float, float]] = [
    (869.759, 115.51),
    (549.471, 502.415),
    (553.139, 0.0),
]
_YONA_VIEWBOX = (1032, 1242)


def _draw_yona_logo(size: int, color: tuple[int, int, int]) -> Image.Image:
    """
    Рисует логотип Yona (молния + треугольник) из Logo.svg.
    Логотип вписывается в квадрат `size × size` так, чтобы оба shape'а
    влезли с небольшим отступом и были по центру.
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    vw, vh = _YONA_VIEWBOX
    # Считаем bbox объединённых полигонов, чтоб фигуру правильно отцентровать.
    all_pts = _YONA_BOLT + _YONA_TRI
    min_x = min(x for x, _ in all_pts)
    max_x = max(x for x, _ in all_pts)
    min_y = min(y for _, y in all_pts)
    max_y = max(y for _, y in all_pts)
    bw = max_x - min_x
    bh = max_y - min_y

    pad = size * 0.08
    avail = size - 2 * pad
    scale = avail / max(bw, bh)
    # Центрирование bbox в квадрате
    off_x = (size - bw * scale) / 2 - min_x * scale
    off_y = (size - bh * scale) / 2 - min_y * scale

    def _tx(poly):
        return [(off_x + x * scale, off_y + y * scale) for x, y in poly]

    d.polygon(_tx(_YONA_BOLT), fill=color)
    d.polygon(_tx(_YONA_TRI), fill=color)
    return img


def render_wallet_qr(address: str, *, size: int = 800) -> bytes:
    """
    Рендерит QR с адресом TON-кошелька. Возвращает PNG байты.

    Параметры:
      address — TON-адрес (например EQDKR...HTuM)
      size    — итоговая ширина картинки в пикселях
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,   # H = 30%, чтобы можно было перекрыть центр иконкой
        box_size=20,
        border=2,
    )
    qr.add_data(address)
    qr.make(fit=True)

    img: Image.Image = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        fill_color=QR_FG_COLOR,
        back_color=QR_BG_COLOR,
    ).convert("RGBA")

    # Подгоняем под нужный размер
    img = img.resize((size, size), Image.LANCZOS)

    # --- Центральная круглая плашка с ракетой ---
    badge_d = int(size * 0.22)        # диаметр плашки
    bx = (size - badge_d) // 2
    by = (size - badge_d) // 2

    # Белая круглая плашка
    badge = Image.new("RGBA", (badge_d, badge_d), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse((0, 0, badge_d - 1, badge_d - 1), fill=QR_BG_COLOR)
    img.paste(badge, (bx, by), badge)

    # Логотип Yona внутри плашки (чуть меньше плашки)
    icon_d = int(badge_d * 0.74)
    logo = _draw_yona_logo(icon_d, QR_FG_COLOR)
    logo_x = (size - icon_d) // 2
    logo_y = (size - icon_d) // 2
    img.paste(logo, (logo_x, logo_y), logo)

    # --- Скруглённые уголки самой картинки ---
    # Добавим небольшую рамку (padding) и закруглим внешние углы.
    pad = int(size * 0.04)
    framed_size = size + pad * 2
    framed = Image.new("RGBA", (framed_size, framed_size), QR_BG_COLOR)
    framed.paste(img, (pad, pad))

    mask = _rounded_corner_mask((framed_size, framed_size), radius=int(framed_size * 0.06))
    out = Image.new("RGBA", (framed_size, framed_size), (255, 255, 255, 0))
    out.paste(framed, (0, 0), mask)

    buf = io.BytesIO()
    out.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
