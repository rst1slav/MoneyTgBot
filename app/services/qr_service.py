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


def _draw_rocket_icon(size: int, color: tuple[int, int, int]) -> Image.Image:
    """
    Простая программная иконка ракеты в брендовом стиле.
    Рисуется на прозрачном фоне. Размер — внутренний (без круглой плашки).
    """
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    s = size
    cx = s / 2

    # Корпус ракеты (вытянутый овал)
    body_w = int(s * 0.34)
    body_h = int(s * 0.62)
    body_x0 = cx - body_w / 2
    body_y0 = s * 0.12
    d.rounded_rectangle(
        (body_x0, body_y0, body_x0 + body_w, body_y0 + body_h),
        radius=int(body_w * 0.45),
        fill=color,
    )

    # Окошко иллюминатора
    win_r = int(s * 0.07)
    win_cy = body_y0 + body_h * 0.35
    d.ellipse(
        (cx - win_r, win_cy - win_r, cx + win_r, win_cy - win_r + win_r * 2),
        fill=QR_BG_COLOR,
    )

    # Крылья по бокам — треугольники
    wing_top = body_y0 + body_h * 0.55
    wing_bot = body_y0 + body_h + s * 0.04
    wing_outer = s * 0.10
    d.polygon([
        (body_x0, wing_top),
        (body_x0 - wing_outer, wing_bot),
        (body_x0 + 2, wing_bot),
    ], fill=color)
    d.polygon([
        (body_x0 + body_w, wing_top),
        (body_x0 + body_w + wing_outer, wing_bot),
        (body_x0 + body_w - 2, wing_bot),
    ], fill=color)

    # Огонь под ракетой — несколько каплевидных треугольников
    flame_top = body_y0 + body_h
    flame_bot = s * 0.96
    flame_left = cx - body_w * 0.32
    flame_right = cx + body_w * 0.32
    d.polygon([
        (flame_left, flame_top),
        (cx, flame_bot),
        (flame_right, flame_top),
    ], fill=color)

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

    # Ракета внутри плашки (чуть меньше плашки)
    icon_d = int(badge_d * 0.78)
    rocket = _draw_rocket_icon(icon_d, QR_FG_COLOR)
    rocket_x = (size - icon_d) // 2
    rocket_y = (size - icon_d) // 2
    img.paste(rocket, (rocket_x, rocket_y), rocket)

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
