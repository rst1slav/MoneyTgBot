"""
Dynamic 'notification card' renderer (Pillow).

Used for:
  - "you received X currency" notifications (ReceivedCard)
  - TON/USD-style rate snapshots (RateCard) — matches the Yona Figma mock
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont


_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_FONTS_DIR = _ASSETS_DIR / "fonts"
_CARDS_ASSETS_DIR = _ASSETS_DIR / "cards"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

# Order matters: first hit wins. Drop HelveticaNeueCyr files in app/assets/fonts/
# for the exact Figma look. Otherwise we fall back to Inter / system bold faces.
_FONT_CANDIDATES_BOLD = [
    _FONTS_DIR / "HelveticaNeueCyr-Bold.ttf",
    _FONTS_DIR / "HelveticaNeueCyr-Heavy.ttf",
    _FONTS_DIR / "Inter-Bold.ttf",
    Path(r"C:\Windows\Fonts\seguibl.ttf"),     # Segoe UI Black
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
]

_FONT_CANDIDATES_MEDIUM = [
    _FONTS_DIR / "HelveticaNeueCyr-Medium.ttf",
    _FONTS_DIR / "HelveticaNeueCyr-Roman.ttf",
    _FONTS_DIR / "Inter-SemiBold.ttf",
    _FONTS_DIR / "Inter-Medium.ttf",
    Path(r"C:\Windows\Fonts\seguisb.ttf"),    # Segoe UI Semibold
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
]


@lru_cache(maxsize=64)
def _font(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    candidates = _FONT_CANDIDATES_BOLD if weight == "bold" else _FONT_CANDIDATES_MEDIUM
    for p in candidates:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                continue
    return ImageFont.load_default()


@lru_cache(maxsize=32)
def _font_inter(size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    """Inter explicitly — for glyphs the HelveticaNeueCyr subset doesn't carry
    (currency symbols, '₿', 'Ξ', minus, etc.)."""
    candidates = (
        _FONTS_DIR / "Inter-Bold.ttf" if weight == "bold" else _FONTS_DIR / "Inter-Medium.ttf",
        _FONTS_DIR / "Inter-SemiBold.ttf",
        _FONTS_DIR / "Inter-Medium.ttf",
    )
    for p in candidates:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size=size)
            except Exception:
                continue
    return _font(size, weight=weight)


# Glyphs the bundled HelveticaNeueCyr subset is missing — fall back to Inter for these.
_NEEDS_INTER = set("€£¥₽₴₸₺₹₩₿ΞßÐŁ−•·")


def _pick_font(text: str, size: int, weight: str = "bold") -> ImageFont.FreeTypeFont:
    """HelveticaNeueCyr for Latin/digits, Inter for exotic glyphs."""
    if any(ch in _NEEDS_INTER for ch in text):
        return _font_inter(size, weight)
    return _font(size, weight)


# ---------------------------------------------------------------------------
# Mini SVG path parser → polygons (Pillow can fill polygons but not paths)
# ---------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r"[MLHVCSQZAmlhvcsqza]|-?\d*\.?\d+(?:[eE][+-]?\d+)?")


def _svg_path_to_polygons(d: str, samples_per_curve: int = 16) -> list[list[tuple[float, float]]]:
    """
    Parse an SVG `d` attribute into a list of polygons (one per subpath).
    Cubic/quadratic bezier curves are flattened. Supports M, L, H, V, C, S, Q, T, Z.
    Arcs (A) are not implemented — none of the icons we use need them.
    """
    tokens = _PATH_TOKEN.findall(d)
    polygons: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    x = y = start_x = start_y = 0.0
    last_ctrl: tuple[float, float] | None = None
    last_qctrl: tuple[float, float] | None = None
    cmd: str | None = None
    i = 0

    def push():
        nonlocal current
        if current:
            polygons.append(current)
        current = []

    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in ("Z", "z"):
                push()
                x, y = start_x, start_y
                last_ctrl = last_qctrl = None
            continue
        if cmd is None:
            i += 1
            continue

        abs_ = cmd.isupper()
        c = cmd.upper()

        if c == "M":
            nx = float(tokens[i]); ny = float(tokens[i + 1]); i += 2
            if not abs_:
                nx += x; ny += y
            push()
            x, y = nx, ny
            start_x, start_y = x, y
            current.append((x, y))
            cmd = "L" if abs_ else "l"
            last_ctrl = last_qctrl = None

        elif c == "L":
            nx = float(tokens[i]); ny = float(tokens[i + 1]); i += 2
            if not abs_:
                nx += x; ny += y
            x, y = nx, ny
            current.append((x, y))
            last_ctrl = last_qctrl = None

        elif c == "H":
            nx = float(tokens[i]); i += 1
            if not abs_:
                nx += x
            x = nx
            current.append((x, y))
            last_ctrl = last_qctrl = None

        elif c == "V":
            ny = float(tokens[i]); i += 1
            if not abs_:
                ny += y
            y = ny
            current.append((x, y))
            last_ctrl = last_qctrl = None

        elif c == "C":
            c1x = float(tokens[i]); c1y = float(tokens[i + 1])
            c2x = float(tokens[i + 2]); c2y = float(tokens[i + 3])
            ex = float(tokens[i + 4]); ey = float(tokens[i + 5]); i += 6
            if not abs_:
                c1x += x; c1y += y
                c2x += x; c2y += y
                ex += x; ey += y
            for s in range(1, samples_per_curve + 1):
                tt = s / samples_per_curve
                mt = 1 - tt
                bx = mt * mt * mt * x + 3 * mt * mt * tt * c1x + 3 * mt * tt * tt * c2x + tt * tt * tt * ex
                by = mt * mt * mt * y + 3 * mt * mt * tt * c1y + 3 * mt * tt * tt * c2y + tt * tt * tt * ey
                current.append((bx, by))
            x, y = ex, ey
            last_ctrl = (c2x, c2y)
            last_qctrl = None

        elif c == "S":
            if last_ctrl is None:
                c1x, c1y = x, y
            else:
                c1x = 2 * x - last_ctrl[0]
                c1y = 2 * y - last_ctrl[1]
            c2x = float(tokens[i]); c2y = float(tokens[i + 1])
            ex = float(tokens[i + 2]); ey = float(tokens[i + 3]); i += 4
            if not abs_:
                c2x += x; c2y += y; ex += x; ey += y
            for s in range(1, samples_per_curve + 1):
                tt = s / samples_per_curve
                mt = 1 - tt
                bx = mt * mt * mt * x + 3 * mt * mt * tt * c1x + 3 * mt * tt * tt * c2x + tt * tt * tt * ex
                by = mt * mt * mt * y + 3 * mt * mt * tt * c1y + 3 * mt * tt * tt * c2y + tt * tt * tt * ey
                current.append((bx, by))
            x, y = ex, ey
            last_ctrl = (c2x, c2y)
            last_qctrl = None

        elif c == "Q":
            qcx = float(tokens[i]); qcy = float(tokens[i + 1])
            ex = float(tokens[i + 2]); ey = float(tokens[i + 3]); i += 4
            if not abs_:
                qcx += x; qcy += y; ex += x; ey += y
            for s in range(1, samples_per_curve + 1):
                tt = s / samples_per_curve
                mt = 1 - tt
                bx = mt * mt * x + 2 * mt * tt * qcx + tt * tt * ex
                by = mt * mt * y + 2 * mt * tt * qcy + tt * tt * ey
                current.append((bx, by))
            x, y = ex, ey
            last_qctrl = (qcx, qcy)
            last_ctrl = None

        elif c == "T":
            if last_qctrl is None:
                qcx, qcy = x, y
            else:
                qcx = 2 * x - last_qctrl[0]
                qcy = 2 * y - last_qctrl[1]
            ex = float(tokens[i]); ey = float(tokens[i + 1]); i += 2
            if not abs_:
                ex += x; ey += y
            for s in range(1, samples_per_curve + 1):
                tt = s / samples_per_curve
                mt = 1 - tt
                bx = mt * mt * x + 2 * mt * tt * qcx + tt * tt * ex
                by = mt * mt * y + 2 * mt * tt * qcy + tt * tt * ey
                current.append((bx, by))
            x, y = ex, ey
            last_qctrl = (qcx, qcy)
            last_ctrl = None

        else:
            # Unknown command — skip remaining tokens for it.
            i += 1

    push()
    return polygons


def _signed_area(pts: list[tuple[float, float]]) -> float:
    """Shoelace formula. Positive = one direction, negative = the other."""
    s = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += (x2 - x1) * (y2 + y1)
    return s


def _draw_svg_paths(
    img: Image.Image,
    d: str,
    *,
    fill: tuple[int, int, int],
    alpha: int = 255,
    at: tuple[float, float],
    scale: float,
    viewbox_offset: tuple[float, float] = (0.0, 0.0),
) -> None:
    """
    Rasterize an SVG path string into `img`.

    Multi-subpath shapes are handled via winding-rule heuristics: subpaths with
    the same winding direction as the first one are filled solid; subpaths with
    the opposite direction are treated as holes (cut out of the mask). That
    correctly handles both:
      - the TON logo (outer + 2 inner triangles, all same orientation → all filled)
      - the fire icon (outer + 1 inner counter-rotated subpath → hole)
    """
    polygons = _svg_path_to_polygons(d)
    if not polygons:
        return
    ox, oy = at
    vx, vy = viewbox_offset

    transformed = [
        [(ox + (px - vx) * scale, oy + (py - vy) * scale) for px, py in poly]
        for poly in polygons
        if len(poly) >= 3
    ]
    if not transformed:
        return

    first_sign = 1 if _signed_area(transformed[0]) >= 0 else -1

    mask = Image.new("L", img.size, 0)
    md = ImageDraw.Draw(mask)
    for poly in transformed:
        same_sign = (1 if _signed_area(poly) >= 0 else -1) == first_sign
        md.polygon(poly, fill=255 if same_sign else 0)

    layer = Image.new("RGBA", img.size, (*fill, alpha))
    if img.mode == "RGBA":
        img.paste(layer, (0, 0), mask)
    else:
        img.paste(layer.convert("RGB"), (0, 0), mask)


# ---------------------------------------------------------------------------
# "Received X currency" card (kept compatible with previous API)
# ---------------------------------------------------------------------------

_THEME_BY_CURRENCY: dict[str, dict] = {
    "USDT": {"bg": (38, 158, 138), "fg": (255, 255, 255), "icon": "T"},
    "TON":  {"bg": (40, 116, 219), "fg": (255, 255, 255), "icon": "T"},
    "USD":  {"bg": (43, 110, 70), "fg": (255, 255, 255), "icon": "$"},
}
_DEFAULT_THEME = {"bg": (60, 70, 85), "fg": (255, 255, 255), "icon": "•"}

_CARD_W = 1024
_CARD_H = 576


@dataclass
class ReceivedCard:
    amount: str        # "8.009821"
    currency: str      # "USDT"
    usd_label: str     # "$ 8"


def render_received_card(card: ReceivedCard) -> bytes:
    theme = _THEME_BY_CURRENCY.get(card.currency.upper(), _DEFAULT_THEME)
    bg, fg, icon_char = theme["bg"], theme["fg"], theme["icon"]

    img = Image.new("RGB", (_CARD_W, _CARD_H), bg)
    draw = ImageDraw.Draw(img)

    wm_font = _font(80)
    wm_color = tuple(max(0, c - 12) for c in bg)
    for y in range(0, _CARD_H, 130):
        offset = 65 if (y // 130) % 2 else 0
        for x in range(-65 + offset, _CARD_W, 130):
            draw.text((x, y), icon_char, font=wm_font, fill=wm_color)

    big_font = _font(170)
    big_text = card.usd_label
    big_bbox = draw.textbbox((0, 0), big_text, font=big_font)
    big_w = big_bbox[2] - big_bbox[0]
    big_h = big_bbox[3] - big_bbox[1]
    big_x = (_CARD_W - big_w) // 2
    big_y = int(_CARD_H * 0.30) - big_h // 2
    draw.text((big_x, big_y), big_text, font=big_font, fill=fg)

    small_font = _font(72)
    amount_text = f"{card.amount} {card.currency.upper()}"
    icon_w = draw.textbbox((0, 0), icon_char, font=small_font)[2]
    amt_bbox = draw.textbbox((0, 0), amount_text, font=small_font)
    amt_w = amt_bbox[2] - amt_bbox[0]
    gap = 28
    total_w = icon_w + gap + amt_w
    start_x = (_CARD_W - total_w) // 2
    y = int(_CARD_H * 0.60)
    draw.text((start_x, y), icon_char, font=small_font, fill=fg)
    draw.text((start_x + icon_w + gap, y), amount_text, font=small_font, fill=fg)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def cache_path_for(card: ReceivedCard) -> Path:
    safe_amt = card.amount.replace(".", "_").replace(" ", "")
    safe_ccy = card.currency.upper()
    safe_usd = card.usd_label.replace("$", "").replace(" ", "").replace(".", "_")
    return Path("cards") / f"received_{safe_ccy}_{safe_amt}_{safe_usd}.png"


def render_to_disk(card: ReceivedCard) -> Path:
    path = cache_path_for(card)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(render_received_card(card))
    return path


# ---------------------------------------------------------------------------
# Rate card — TON/USD-style with gradient bg + watermark + SVG-icon paths
# ---------------------------------------------------------------------------

# Icon SVG paths copied verbatim from the Figma React export. ViewBox sizes
# noted next to each so we can scale them precisely.
_SVG_TON_VIEWBOX = (90, 90)
_SVG_TON_PATH = (
    "M60.3697 24.75H29.6385C23.988 24.75 20.4067 30.8452 23.2493 35.7724L42.2156 "
    "68.6462C43.4532 70.7927 46.5549 70.7927 47.7925 68.6462L66.7626 35.7724"
    "C69.6014 30.8529 66.0201 24.75 60.3735 24.75H60.3697ZM42.2001 58.7879"
    "L38.0696 50.7938L28.1031 32.9685C27.4456 31.8275 28.2578 30.3656 29.6346 "
    "30.3656H42.1963V58.7918L42.2001 58.7879ZM61.8973 32.9646L51.9346 50.7977"
    "L47.8041 58.7879V30.3618H60.3658C61.7426 30.3618 62.5548 31.8237 61.8973 "
    "32.9646Z"
)

_SVG_USD_DOLLAR_VIEWBOX = (90, 90)
_SVG_USD_DOLLAR_PATH = (
    "M44.8436 71.5826C43.3084 71.5826 42.3133 70.616 42.3133 68.9955V66.124"
    "C36.2861 65.4132 32.1068 62.3143 30.8843 58.3625C30.6853 57.8223 30.6 "
    "57.2821 30.6 56.7988C30.6 55.0077 31.8225 53.8136 33.7842 53.8136"
    "C35.4332 53.8136 36.3714 54.7803 36.9684 56.1165C38.1056 59.0164 40.7781 "
    "60.6085 45.0995 60.6085C49.6768 60.6085 52.6051 58.7889 52.6051 55.4057"
    "C52.6051 52.5343 49.9895 51.0559 45.6397 50.0324L41.7732 49.1226"
    "C35.291 47.6442 30.998 43.9483 30.998 38.5749C30.998 32.1497 35.9449 "
    "28.2831 42.3133 27.4871V24.5872C42.3133 22.9666 43.3084 22 44.8436 22"
    "C46.3789 22 47.3739 22.9666 47.3739 24.5872V27.4871C52.9463 28.1694 "
    "56.9834 31.183 58.2344 35.3623C58.3765 35.9025 58.4902 36.4142 58.4902 "
    "36.9544C58.4902 38.6034 57.2393 39.5984 55.4198 39.5984C53.7708 39.5984 "
    "52.8894 38.8024 52.1787 37.4377C50.8993 34.4525 48.6817 33.0026 44.8721 "
    "33.0026C40.5222 33.0026 37.9066 34.9358 37.9066 37.9779C37.9066 40.5935 "
    "40.4654 42.214 44.2466 43.0669L47.9426 43.9198C55.2492 45.5972 59.4 "
    "49.151 59.4 54.7234C59.4 61.7173 53.8277 65.4701 47.3739 66.1809V68.9955"
    "C47.3739 70.616 46.3789 71.5826 44.8436 71.5826Z"
)

_SVG_FIRE_VIEWBOX = (56, 72)
_SVG_FIRE_PATH = (
    "M26.1637 72C20.9043 72 16.3107 71.0569 12.3828 69.1706C8.47712 67.2843 "
    "5.43689 64.6436 3.26214 61.2483C1.08738 57.853 0 53.8807 0 49.3315"
    "C0 46.9348 0.188627 44.8044 0.565881 42.9404C0.965326 41.0763 1.44244 "
    "39.4008 1.99723 37.914C2.5742 36.405 3.15118 34.9847 3.72816 33.6533"
    "C4.30513 32.3218 4.78225 31.0014 5.1595 29.6921C5.53675 28.3828 5.72538 "
    "26.9847 5.72538 25.4979C5.72538 24.7656 5.681 23.9667 5.59223 23.1012"
    "C5.52566 22.2136 5.49237 21.5589 5.49237 21.1373C5.49237 20.405 5.70319 "
    "19.8058 6.12483 19.3398C6.54646 18.8738 7.10125 18.6408 7.78918 18.6408"
    "C8.96533 18.6408 10.1526 18.9958 11.3509 19.706C12.5714 20.4161 13.6588 "
    "21.4036 14.613 22.6685C15.5895 23.9112 16.3329 25.3537 16.8433 26.9958"
    "L15.1789 27.3287C15.7115 26.3967 16.0444 25.5756 16.1775 24.8655"
    "C16.3329 24.1331 16.4105 23.3897 16.4105 22.6352C16.3884 20.4383 15.9778 "
    "18.3523 15.1789 16.3773C14.4022 14.4022 13.4036 12.527 12.1831 10.7517"
    "C10.9847 8.97642 9.73093 7.30097 8.42164 5.72538C8.06657 5.32594 7.80028 "
    "4.9154 7.62275 4.49376C7.44522 4.07212 7.35645 3.65049 7.35645 3.22885"
    "C7.35645 2.16366 7.81137 1.36477 8.72122 0.832178C9.65326 0.277392 "
    "10.9071 0 12.4827 0C15.4119 0 18.4965 0.355062 21.7365 1.06519"
    "C24.9986 1.75312 28.2275 2.8405 31.423 4.32732C34.6408 5.79196 37.6921 "
    "7.66713 40.577 9.95284C43.4619 12.2164 46.0139 14.9126 48.233 18.0416"
    "C50.4743 21.1484 52.2386 24.7101 53.5257 28.7268C54.8128 32.7434 55.4563 "
    "37.2372 55.4563 42.208C55.4563 46.6685 54.7573 50.7295 53.3592 54.3911"
    "C51.9834 58.0527 49.9972 61.1928 47.4008 63.8114C44.8044 66.43 41.7087 "
    "68.4494 38.1137 69.8696C34.5409 71.2899 30.5576 72 26.1637 72ZM26.9293 "
    "62.6463C29.57 62.6463 31.7559 62.0693 33.4868 60.9154C35.2399 59.7614 "
    "36.5492 58.2413 37.4147 56.3551C38.2802 54.4688 38.7129 52.4272 38.7129 "
    "50.2302C38.7129 48.0555 38.2913 45.8696 37.448 43.6727C36.6269 41.4757 "
    "35.3953 39.4452 33.7531 37.5811C32.111 35.7171 30.0693 34.208 27.6283 "
    "33.0541C27.473 32.9875 27.3509 32.9986 27.2621 33.0874C27.1734 33.154 "
    "27.1401 33.2649 27.1623 33.4202C27.4508 36.1498 27.4064 38.6907 27.0291 "
    "41.043C26.6519 43.3731 26.0194 45.1373 25.1318 46.3356C24.7101 45.3814 "
    "24.233 44.4938 23.7004 43.6727C23.19 42.8294 22.5465 42.0638 21.7698 "
    "41.3759C21.6588 41.2871 21.5479 41.2649 21.4369 41.3093C21.3481 41.3315 "
    "21.2927 41.4202 21.2705 41.5756C21.1373 42.5298 20.8266 43.4064 20.3384 "
    "44.2053C19.8502 44.982 19.3176 45.8031 18.7406 46.6685C18.1637 47.5118 "
    "17.6533 48.466 17.2094 49.5312C16.7878 50.5742 16.577 51.828 16.577 "
    "53.2926C16.577 56.0888 17.5201 58.3523 19.4064 60.0832C21.3148 61.792 "
    "23.8225 62.6463 26.9293 62.6463Z"
)

_SVG_DOLLAR_VIEWBOX = (75, 129)
_SVG_DOLLAR_PATH = (
    "M37.0928 129C33.0948 129 30.5035 126.485 30.5035 122.269V114.798"
    "C14.8075 112.949 3.92399 104.886 0.740375 94.6049C0.222113 93.1995 0 "
    "91.7942 0 90.5367C0 85.8767 3.18361 82.7701 8.2922 82.7701C12.5864 "
    "82.7701 15.0296 85.285 16.5844 88.7615C19.5459 96.3062 26.5054 100.448 "
    "37.7591 100.448C49.6792 100.448 57.305 95.7145 57.305 86.9123C57.305 "
    "79.4415 50.4936 75.5952 39.1658 72.9323L29.0967 70.5654C12.2162 66.719 "
    "1.03653 57.1032 1.03653 43.1233C1.03653 26.4065 13.9191 16.3469 30.5035 "
    "14.2758V6.73108C30.5035 2.51491 33.0948 0 37.0928 0C41.0908 0 43.6821 "
    "2.51491 43.6821 6.73108V14.2758C58.1935 16.051 68.7068 23.8916 71.9645 "
    "34.7649C72.3346 36.1703 72.6308 37.5017 72.6308 38.9071C72.6308 43.1972 "
    "69.3731 45.7861 64.6347 45.7861C60.3406 45.7861 58.0454 43.715 56.1945 "
    "40.1646C52.8628 32.3979 47.0879 28.6256 37.1668 28.6256C25.8391 28.6256 "
    "19.0276 33.6554 19.0276 41.57C19.0276 48.375 25.691 52.5912 35.538 "
    "54.8102L45.1629 57.0292C64.1905 61.3934 75 70.6393 75 85.137C75 103.333 "
    "60.4886 113.097 43.6821 114.946V122.269C43.6821 126.485 41.0908 129 "
    "37.0928 129Z"
)


# Palette — taken straight from the Yona.svg export (Figma).
_RATE_GREEN_TOP = (92, 221, 125)     # #5CDD7D — bg gradient top, gain state
_RATE_GREEN_BOT = (43, 188, 80)      # #2BBC50 — bg gradient bottom, gain state
_RATE_RED_TOP = (255, 110, 113)      # #FF6E71 — bg gradient top, loss state
_RATE_RED_BOT = (227, 51, 55)        # #E33337 — bg gradient bottom, loss state
_RATE_DARK_TOP = (37, 34, 34)        # #252222 — bg gradient top, neutral state
_RATE_DARK_BOT = (16, 12, 12)        # #100C0C — bg gradient bottom, neutral state
_RATE_GREEN = (35, 180, 110)         # #23B46E — chart + change%
_RATE_FIRE = (252, 145, 49)          # #FC9131 — flame on gain
_RATE_FROST = (60, 130, 230)         # cool blue flame on loss (we colour-shift, SVG has none)
_RATE_GRAY = (179, 179, 179)         # #B3B3B3 — labels, USD coin
_RATE_DOLLAR_INK = (140, 140, 140)   # #8C8C8C — slightly darker for the price-row $
_RATE_INK = (16, 12, 12)             # #100C0C — price number
_RATE_TON = (0, 152, 234)            # #0098EA
_RATE_TITLE_OPACITY = 0.3            # black @ 30% opacity for "TON/USD"
_RATE_GRID_OPACITY = 0.05            # black @ 5% opacity for dashed grid
_RATE_CARD_BG = (255, 255, 255)

# Canvas matches the SVG export exactly so everything inherits the right proportions.
_RATE_W = 1600
_RATE_H = 1000
_RATE_CARD_X = 100
_RATE_CARD_Y = 100
_RATE_CARD_W = 1400
_RATE_CARD_H = 800
_RATE_CARD_R = 90


@dataclass
class RateCard:
    base: str
    quote: str
    price: float
    change_pct: float
    history: list[float] = field(default_factory=list)
    date_labels: list[str] = field(default_factory=list)

    def cache_key(self) -> str:
        h = ",".join(f"{p:.4f}" for p in self.history)
        d = ",".join(self.date_labels)
        return f"rate|{self.base}|{self.quote}|{self.price:.4f}|{self.change_pct:.2f}|{h}|{d}"


def _vertical_gradient(w: int, h: int, top: tuple, bottom: tuple) -> Image.Image:
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        d.line([(0, y), (w, y)], fill=(r, g, b))
    return img


# Lightning-bolt + small-triangle tile copied verbatim from Yona.svg (coords
# normalised so the tile starts at (0, 0); the two shapes together fill an
# ~90×100 region and the SVG tiles them on a ~181×150 grid with row shift).
_WM_BOLT_POLY: list[tuple[float, float]] = [
    (0.0, 32.836), (19.7964, 75.32), (22.5704, 100), (40.5465, 82.88),
    (83.0, 63.069), (66.5031, 27.664), (41.5001, 47.952), (35.378, 16.327),
]
_WM_TRI_POLY: list[tuple[float, float]] = [
    (70.0004, 9.304), (44.2228, 40.466), (44.518, 0.0),
]


_WM_SCALE = 1.3   # 30% larger tiles per feedback


def _draw_wm_tile(d: ImageDraw.ImageDraw, ox: float, oy: float, fill: tuple) -> None:
    d.polygon([(ox + x * _WM_SCALE, oy + y * _WM_SCALE) for x, y in _WM_BOLT_POLY], fill=fill)
    d.polygon([(ox + x * _WM_SCALE, oy + y * _WM_SCALE) for x, y in _WM_TRI_POLY], fill=fill)


@lru_cache(maxsize=4)
def _watermark_layer(w: int, h: int) -> Image.Image:
    """
    Programmatic watermark matching the Yona.svg pattern.

    The SVG ships two interleaved rows:
      • A-rows: tile origin y-anchored at 935 in SVG coords, x positions every
        181 px starting at 4.
      • B-rows: y = A_y − 50, x positions every 181 px starting at 65
        (shifted +61 px horizontally).
      • A-rows repeat every 150 px vertically; B-rows live exactly halfway
        between each pair of A-rows.
    """
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    fill = (255, 255, 255, int(0.09 * 255))  # slightly less opaque (was 0.12)

    # Larger tiles → spread them ~30% farther apart so they don't overlap.
    row_step = int(300 * _WM_SCALE)   # ≈ 390
    col_step = int(362 * _WM_SCALE)   # ≈ 470
    sec_offset_y = int(150 * _WM_SCALE)
    sec_offset_x = int(181 * _WM_SCALE)

    base_a_y = 935
    a_y = base_a_y
    while a_y > -200:
        a_y -= row_step
    while a_y < h + 200:
        x = 4
        while x > -200:
            x -= col_step
        while x < w + 100:
            _draw_wm_tile(d, x, a_y, fill)
            x += col_step
        sec_y = a_y - sec_offset_y
        sx = 4 + sec_offset_x
        while sx > -200:
            sx -= col_step
        while sx < w + 100:
            _draw_wm_tile(d, sx, sec_y, fill)
            sx += col_step
        a_y += row_step
    return layer


def _rounded_rect_mask(w: int, h: int, radius: int) -> Image.Image:
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    return mask


def _smooth(points: list[tuple[float, float]], samples: int = 8) -> list[tuple[float, float]]:
    if len(points) < 2:
        return list(points)
    pts = [points[0]] + list(points) + [points[-1]]
    out: list[tuple[float, float]] = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(samples):
            t = s / samples
            t2, t3 = t * t, t * t * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    out.append(points[-1])
    return out


def _dashed_hline(
    img: Image.Image,
    x0: int, x1: int, y: int,
    color: tuple[int, int, int], alpha: int = 32,
    dash: int = 16, gap: int = 12, width: int = 3,
) -> None:
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = x0
    while x < x1:
        x_end = min(x + dash, x1)
        d.line([(x, y), (x_end, y)], fill=(*color, alpha), width=width)
        x = x_end + gap
    img.alpha_composite(layer)


def _coin_with_path(
    diameter: int,
    bg: tuple[int, int, int],
    glyph_path: str,
    glyph_viewbox: tuple[int, int],
) -> Image.Image:
    """Circle + inner SVG-path glyph centred."""
    img = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, diameter - 1, diameter - 1), fill=bg)
    vw, vh = glyph_viewbox
    scale = diameter / max(vw, vh)
    _draw_svg_paths(
        img,
        glyph_path,
        fill=(255, 255, 255),
        at=((diameter - vw * scale) / 2, (diameter - vh * scale) / 2),
        scale=scale,
    )
    return img


def _theme_for(change_pct: float) -> tuple[tuple, tuple, tuple, tuple]:
    """Returns (bg_top, bg_bot, pct_color, flame_color) for the change direction."""
    if change_pct >= 0:
        return _RATE_GREEN_TOP, _RATE_GREEN_BOT, _RATE_GREEN, _RATE_FIRE
    # Loss: chart label colour is the chart red (#EA3943) so it's visible on the white card.
    return _RATE_RED_TOP, _RATE_RED_BOT, (234, 57, 67), (255, 230, 220)


# ---------------------------------------------------------------------------
# Ticker → icon / symbol metadata.
# Fiat = always gray badge with a currency symbol. Crypto = brand colour with a
# short glyph (or the SVG path for TON). Unknown tickers → gray "?" badge.
# ---------------------------------------------------------------------------

_FIAT_SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "RUB": "₽", "UAH": "₴", "BYN": "Br", "PLN": "zł", "UZS": "сум",
    "CAD": "$", "AUD": "$", "CHF": "Fr", "KZT": "₸", "TRY": "₺",
    "INR": "₹", "KRW": "₩", "BRL": "R$", "MXN": "$",
}

# Brand colour + (optional) single-glyph override. None glyph → use first letter.
_CRYPTO_BRAND: dict[str, tuple[tuple[int, int, int], str | None]] = {
    # Majors
    "TON":   ((0, 152, 234),    None),   # rendered via _SVG_TON_PATH
    "BTC":   ((247, 147, 26),   "₿"),
    "ETH":   ((98, 126, 234),   "Ξ"),
    "BNB":   ((240, 185, 11),   "B"),
    "SOL":   ((153, 69, 255),   "S"),
    "XRP":   ((35, 41, 47),     "X"),
    "ADA":   ((11, 99, 207),    "A"),
    "DOGE":  ((188, 159, 71),   "Ð"),
    "TRX":   ((255, 6, 10),     "T"),
    "LINK":  ((37, 90, 220),    "L"),
    "AVAX":  ((227, 32, 50),    "A"),
    "MATIC": ((130, 71, 229),   "M"),
    "DOT":   ((230, 0, 122),    "•"),
    "LTC":   ((189, 189, 189),  "Ł"),
    # Stablecoins (still gray-ish but keep their brand greens/blues)
    "USDT":  ((38, 161, 123),   "T"),
    "USDC":  ((39, 117, 202),   "C"),
    "BUSD":  ((240, 185, 11),   "B"),
    "DAI":   ((247, 184, 51),   "D"),
    # Popular TON memecoins / jettons
    "DOGS":  ((232, 178, 39),   "D"),
    "NOT":   ((28, 28, 28),     "N"),
    "MAJOR": ((175, 102, 235),  "M"),
    "WIF":   ((194, 144, 96),   "W"),
    "PEPE":  ((105, 175, 53),   "P"),
    "BOLT":  ((46, 192, 222),   "B"),
    "REDO":  ((228, 80, 80),    "R"),
    "DURIK": ((212, 165, 73),   "D"),
    "FPI":   ((63, 105, 224),   "F"),
    "UTYA":  ((150, 197, 92),   "U"),
    "STON":  ((26, 102, 235),   "S"),
    "JETTON":((100, 100, 255),  "J"),
    "HMSTR": ((255, 180, 60),   "H"),
    "CATI":  ((250, 200, 100),  "C"),
}


def _is_fiat(ticker: str) -> bool:
    return ticker.upper() in _FIAT_SYMBOLS


def _is_dollar_pegged(ticker: str) -> bool:
    return ticker.upper() in {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDD", "USD"}


def _coin_with_glyph(
    diameter: int,
    bg: tuple[int, int, int],
    glyph: str,
    fg: tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Circle badge with a font-rendered glyph centred."""
    img = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, diameter - 1, diameter - 1), fill=bg)
    glyph_font = _pick_font(glyph, int(diameter * 0.55), weight="bold")
    bbox = d.textbbox((0, 0), glyph, font=glyph_font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(
        ((diameter - tw) // 2 - bbox[0], (diameter - th) // 2 - bbox[1]),
        glyph, font=glyph_font, fill=fg,
    )
    return img


def _coin_badge_for(ticker: str, diameter: int) -> Image.Image:
    """Round badge for a ticker. Crypto = brand colour, fiat = gray, unknown = gray '?'."""
    t = (ticker or "").upper()
    if t == "TON":
        return _coin_with_path(diameter, _RATE_TON, _SVG_TON_PATH, _SVG_TON_VIEWBOX)
    if t in _CRYPTO_BRAND:
        color, glyph_override = _CRYPTO_BRAND[t]
        return _coin_with_glyph(diameter, color, glyph_override or t[0])
    if t in _FIAT_SYMBOLS:
        return _coin_with_glyph(diameter, _RATE_GRAY, _FIAT_SYMBOLS[t])
    return _coin_with_glyph(diameter, _RATE_GRAY, "?")


def _price_prefix(ticker: str) -> str:
    """Symbol/abbreviation to render before the price number."""
    t = (ticker or "").upper()
    if t in _FIAT_SYMBOLS:
        return _FIAT_SYMBOLS[t]
    if _is_dollar_pegged(t):
        return "$"
    return t  # e.g. "BTC", "ETH", "DOGS"


def _render_glyph_at_height(
    glyph: str,
    *,
    target_h_px: int,
    color: tuple[int, int, int],
) -> Image.Image | None:
    """
    Render `glyph` to its own RGBA layer, trim to ink bounds, then resize so the
    ink height = `target_h_px`. Makes $/€/₴/£/etc. all line up at the same
    visual height irrespective of per-glyph proportions in the font.
    """
    if not glyph:
        return None
    # Render large to allow clean downsampling.
    big = max(target_h_px * 2, 100)
    font = _pick_font(glyph, big, weight="bold")
    canvas = Image.new("RGBA", (big * 3, big * 3), (0, 0, 0, 0))
    cd = ImageDraw.Draw(canvas)
    cd.text((big, big), glyph, font=font, fill=color, anchor="lt")
    ink = canvas.getbbox()
    if not ink:
        return None
    cropped = canvas.crop(ink)
    if cropped.height == 0:
        return None
    scale = target_h_px / cropped.height
    new_w = max(1, int(cropped.width * scale))
    return cropped.resize((new_w, target_h_px), Image.LANCZOS)


# CMC-style chart colours.
_CHART_GREEN = (35, 180, 110)        # line + fill above baseline
_CHART_RED = (234, 57, 67)           # line + fill below baseline


def _split_polyline_at(
    points: list[tuple[float, float]], baseline_y: float
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """
    Split `points` (a polyline) into segments above and below `baseline_y`.
    At each baseline crossing the segment is closed at the interpolated point and
    a new segment is started at that same point. Lower y == higher price.

    Returns (above_segments, below_segments).
    """
    above: list[list[tuple[float, float]]] = []
    below: list[list[tuple[float, float]]] = []
    if not points:
        return above, below

    current_above = points[0][1] <= baseline_y
    current_seg: list[tuple[float, float]] = [points[0]]

    for i in range(1, len(points)):
        prev = current_seg[-1]
        curr = points[i]
        curr_above = curr[1] <= baseline_y
        if curr_above == current_above:
            current_seg.append(curr)
            continue
        # Crossing — interpolate intersection with baseline.
        dy = curr[1] - prev[1]
        t = 0.0 if abs(dy) < 1e-9 else (baseline_y - prev[1]) / dy
        mid = (prev[0] + t * (curr[0] - prev[0]), baseline_y)
        current_seg.append(mid)
        (above if current_above else below).append(current_seg)
        current_seg = [mid, curr]
        current_above = curr_above

    (above if current_above else below).append(current_seg)
    return above, below


def _fill_segment_with_vertical_gradient(
    img: Image.Image,
    segment: list[tuple[float, float]],
    baseline_y: float,
    *,
    color: tuple[int, int, int],
    line_alpha: int = 26,   # ~10% opacity at the line, fading to 0 at baseline
) -> None:
    """
    Fill the area between `segment` (polyline) and `baseline_y`. The gradient is
    always strongest next to the LINE (the segment polyline) and fades to 0 at
    the baseline. Works regardless of whether the segment is above or below the
    baseline.
    """
    if len(segment) < 2:
        return
    W, H = img.size
    seg_ys = [y for _, y in segment]
    seg_min = min(seg_ys)
    seg_max = max(seg_ys)
    above = seg_min <= baseline_y and seg_max <= baseline_y
    below = seg_min >= baseline_y and seg_max >= baseline_y

    # Determine where the line edge of the area sits and where the baseline edge sits.
    if above:
        # Area between seg (top) and baseline (bottom). Line at seg_min y, baseline at baseline_y.
        line_y = seg_min
        base_y = baseline_y
    elif below:
        # Area between baseline (top) and seg (bottom). Line at seg_max y, baseline at baseline_y.
        line_y = seg_max
        base_y = baseline_y
    else:
        # Mixed (shouldn't happen if caller split correctly) — pick the farthest from baseline.
        line_y = seg_min if abs(seg_min - baseline_y) > abs(seg_max - baseline_y) else seg_max
        base_y = baseline_y

    if abs(line_y - base_y) < 1:
        return

    grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    span = abs(line_y - base_y)
    lo_y = int(min(line_y, base_y))
    hi_y = int(max(line_y, base_y))
    for y in range(lo_y, hi_y + 1):
        # t=0 at the line, t=1 at the baseline → alpha = line_alpha at line, 0 at baseline.
        t = abs(y - line_y) / span
        alpha = max(0, min(255, int(line_alpha * (1.0 - t))))
        gd.line([(0, y), (W, y)], fill=(*color, alpha))

    mask = Image.new("L", (W, H), 0)
    poly = list(segment) + [(segment[-1][0], baseline_y), (segment[0][0], baseline_y)]
    ImageDraw.Draw(mask).polygon(poly, fill=255)

    r, g, b, a = grad.split()
    grad_clipped = Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))
    img.alpha_composite(grad_clipped)


def render_rate_card(card: RateCard) -> bytes:
    """
    1:1 with Yona.svg (1600×1000, card 1400×800 at (100,100,rx=120)).
    Static positions and colours are taken straight from the export. Renders at
    2× supersampling then downsamples with LANCZOS for crisp icons / lines.
    """
    SS = 2  # supersampling factor — render double-size, then resize down
    s = lambda v: int(round(v * SS))   # noqa: E731  scale helper

    W, H = _RATE_W * SS, _RATE_H * SS

    bg_top, bg_bot, pct_color, flame_color = _theme_for(card.change_pct)

    # --- 1. Background gradient + watermark layer ---
    bg = _vertical_gradient(W, H, bg_top, bg_bot).convert("RGBA")
    bg.alpha_composite(_watermark_layer(W, H))

    # --- 2. Card drop shadow (SVG: dy=4 stdDev=12.5 alpha=0.1) ---
    shadow_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow_layer).rounded_rectangle(
        (s(_RATE_CARD_X), s(_RATE_CARD_Y + 4),
         s(_RATE_CARD_X + _RATE_CARD_W), s(_RATE_CARD_Y + _RATE_CARD_H + 4)),
        radius=s(_RATE_CARD_R),
        fill=(0, 0, 0, int(0.10 * 255)),
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(12.5 * SS))
    bg.alpha_composite(shadow_layer)

    # --- 3. White rounded card ---
    card_rect = (
        s(_RATE_CARD_X), s(_RATE_CARD_Y),
        s(_RATE_CARD_X + _RATE_CARD_W) - 1, s(_RATE_CARD_Y + _RATE_CARD_H) - 1,
    )
    ImageDraw.Draw(bg).rounded_rectangle(card_rect, radius=s(_RATE_CARD_R), fill=(255, 255, 255, 255))

    img = bg

    # --- 4. Top row icons — dynamic per ticker. Crypto coloured, fiat gray. ---
    base_icon = _coin_badge_for(card.base, s(90))
    quote_icon = _coin_badge_for(card.quote, s(90))
    img.alpha_composite(base_icon, (s(175), s(175)))
    img.alpha_composite(quote_icon, (s(235), s(175)))

    # --- 5. Title — "TON / USD". TON and USD at 30% black; only the / at 20%. ---
    base_text = card.base.upper()
    quote_text = card.quote.upper()
    title_font = _font(s(80), weight="bold")
    title_x = s(346)
    title_y = s(176)
    title_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    tdrw = ImageDraw.Draw(title_layer)
    fill_30 = (0, 0, 0, int(0.30 * 255))    # back to original 30%
    fill_20 = (0, 0, 0, int(0.20 * 255))    # only the slash uses 20%
    # "TON"
    tdrw.text((title_x, title_y), base_text, font=title_font, fill=fill_30, anchor="lt")
    ton_w = tdrw.textbbox((title_x, title_y), base_text, font=title_font, anchor="lt")[2] - title_x
    # " / " — leading + trailing space stay attached to the slash; renders 20%.
    slash_text = " / "
    slash_x = title_x + ton_w
    tdrw.text((slash_x, title_y), slash_text, font=title_font, fill=fill_20, anchor="lt")
    slash_w = tdrw.textbbox((slash_x, title_y), slash_text, font=title_font, anchor="lt")[2] - slash_x
    # "USD"
    usd_x = slash_x + slash_w
    tdrw.text((usd_x, title_y), quote_text, font=title_font, fill=fill_30, anchor="lt")
    img.alpha_composite(title_layer)

    # --- 6. "+ X%" right-aligned. Fire only when growth > 25%. ---
    # Trailing zeros stripped: "8.00%" → "8%", "8.10%" → "8.1%".
    sign = "+ " if card.change_pct >= 0 else "- "
    _pct_num = f"{abs(card.change_pct):.2f}"
    if "." in _pct_num:
        _pct_num = _pct_num.rstrip("0").rstrip(".")
    pct_text = f"{sign}{_pct_num}%"
    pct_font = _font(s(80), weight="bold")
    show_fire = card.change_pct > 25.0
    right_edge = s(1425)
    flame_h_logical = 72
    flame_w_logical = int(flame_h_logical * (_SVG_FIRE_VIEWBOX[0] / _SVG_FIRE_VIEWBOX[1]))
    if show_fire:
        flame_x = right_edge - s(flame_w_logical)
        flame_y = s(184)
        _draw_svg_paths(
            img, _SVG_FIRE_PATH,
            fill=flame_color,
            at=(flame_x, flame_y),
            scale=s(flame_h_logical) / _SVG_FIRE_VIEWBOX[1],
        )
        pct_right_x = flame_x - s(20)
    else:
        pct_right_x = right_edge

    drw = ImageDraw.Draw(img)
    pbbox = drw.textbbox((0, 0), pct_text, font=pct_font)
    pct_w = pbbox[2] - pbbox[0]
    drw.text((pct_right_x - pct_w, s(176)), pct_text, font=pct_font, fill=pct_color)

    # --- 7. Big currency-prefix then price number ---
    # Render every prefix the same way (font → trim → scale to a target visual
    # height) so $/€/£/¥/₽/₴/etc. line up at the same physical size.
    prefix = _price_prefix(card.quote)
    prefix_x_logical = 175
    prefix_y_top_logical = 307
    target_prefix_h_logical = 129          # match the original SVG dollar ink-height
    drw = ImageDraw.Draw(img)

    prefix_layer = _render_glyph_at_height(
        prefix,
        target_h_px=s(target_prefix_h_logical),
        color=_RATE_DOLLAR_INK,
    )
    if prefix_layer is not None:
        img.alpha_composite(prefix_layer, (s(prefix_x_logical), s(prefix_y_top_logical)))
        prefix_w = prefix_layer.width
    else:
        prefix_w = 0

    # The price number itself — adaptive format so micro-prices keep precision.
    # Trailing zeros are stripped: "1.90" → "1.9", "1.00" → "1".
    price_font = _font(s(160), weight="bold")
    if abs(card.price) >= 1:
        price_str = f"{card.price:,.2f}"
        if "." in price_str:
            price_str = price_str.rstrip("0").rstrip(".")
    elif abs(card.price) >= 0.0001:
        price_str = f"{card.price:.4g}"      # e.g. "0.0003"
    else:
        price_str = f"{card.price:.2e}"      # tiny values → scientific notation
    pbbox = drw.textbbox((0, 0), price_str, font=price_font)
    price_text_y = s(prefix_y_top_logical) - pbbox[1]
    price_text_x = s(prefix_x_logical) + prefix_w + s(22)
    drw.text((price_text_x, price_text_y), price_str, font=price_font, fill=_RATE_INK)

    # --- 8. Chart area ---
    chart_left = s(175)
    chart_top = s(495)
    chart_bottom = s(775)

    history = list(card.history) if len(card.history) >= 2 else [card.price] * 8
    lo, hi = min(history), max(history)
    if hi - lo < 1e-9:
        hi = lo + 1.0
    mid = (lo + hi) / 2
    entry_price = history[0]  # CMC-style baseline = first sample of the window

    # Right-axis labels are computed first so chart_right can shrink to fit wide
    # numbers (BTC-style "117,532"). This way the chart line never overlaps them.
    def _fmt_axis(v: float) -> str:
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        if abs(v) >= 1:
            return f"{v:.2f}"
        # Sub-dollar prices: 4 significant digits.
        return f"{v:.4g}"

    axis_texts = (_fmt_axis(hi), _fmt_axis(mid), _fmt_axis(lo))
    label_font = _font(s(36), weight="medium")
    max_label_w = max(
        drw.textbbox((0, 0), text, font=label_font)[2] for text in axis_texts
    )
    label_right_x = s(1450)
    chart_right = label_right_x - max_label_w - s(24)

    cw = chart_right - chart_left
    ch = chart_bottom - chart_top
    pts = [
        (
            chart_left + (i / (len(history) - 1)) * cw,
            chart_bottom - ((p - lo) / (hi - lo)) * ch,
        )
        for i, p in enumerate(history)
    ]
    smooth = list(pts)  # raw — no Catmull-Rom smoothing

    # 3 dashed grid lines, evenly distributed across the new chart band.
    grid_ys = (chart_top, (chart_top + chart_bottom) // 2, chart_bottom)
    for y in grid_ys:
        _dashed_hline(img, chart_left, chart_right, y,
                      (0, 0, 0), alpha=int(_RATE_GRID_OPACITY * 255),
                      dash=s(12), gap=s(10), width=s(3))

    # Split + colour the line per CMC style.
    baseline_y = chart_bottom - ((entry_price - lo) / (hi - lo)) * ch
    above_segs, below_segs = _split_polyline_at(smooth, baseline_y)

    fill_line_alpha = int(0.20 * 255)   # 20% near the line, fades to 0 at baseline
    for seg in above_segs:
        _fill_segment_with_vertical_gradient(
            img, seg, baseline_y, color=_CHART_GREEN, line_alpha=fill_line_alpha
        )
    for seg in below_segs:
        _fill_segment_with_vertical_gradient(
            img, seg, baseline_y, color=_CHART_RED, line_alpha=fill_line_alpha
        )

    line_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(line_layer)
    for seg in above_segs:
        ld.line([(int(x), int(y)) for x, y in seg],
                fill=(*_CHART_GREEN, 255), width=s(5), joint="curve")
    for seg in below_segs:
        ld.line([(int(x), int(y)) for x, y in seg],
                fill=(*_CHART_RED, 255), width=s(5), joint="curve")
    img.alpha_composite(line_layer)

    # --- 9. Right-side price labels (right-aligned, vertically centred on grid) ---
    label_color = _CHART_GREEN if card.change_pct >= 0 else _CHART_RED
    for text, y in zip(axis_texts, grid_ys):
        drw.text((label_right_x, y), text, font=label_font, fill=label_color, anchor="rm")

    # --- 10. Bottom date labels — anchored so leftmost/rightmost don't spill ---
    labels = card.date_labels or []
    if labels:
        date_font = _font(s(34), weight="bold")
        ly = s(820)
        if len(labels) == 1:
            drw.text((chart_left + (chart_right - chart_left) // 2, ly),
                     labels[0], font=date_font, fill=_RATE_GRAY, anchor="mt")
        else:
            for i, lbl in enumerate(labels):
                x = int(chart_left + i * (chart_right - chart_left) / (len(labels) - 1))
                if i == 0:
                    anchor = "lt"           # left-edge aligned with chart_left
                elif i == len(labels) - 1:
                    anchor = "rt"           # right-edge aligned with chart_right
                else:
                    anchor = "mt"           # centred
                drw.text((x, ly), lbl, font=date_font, fill=_RATE_GRAY, anchor=anchor)

    # --- 11. Downsample to target size for crisp output ---
    if SS != 1:
        img = img.resize((_RATE_W, _RATE_H), Image.LANCZOS)

    out = io.BytesIO()
    img.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_rate_card_to_disk(card: RateCard) -> Path:
    safe = card.cache_key().replace("|", "_").replace(",", "-")[:160]
    path = Path("cards") / f"rate_{safe}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(render_rate_card(card))
    return path
