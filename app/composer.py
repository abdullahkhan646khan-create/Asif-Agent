"""Turns the AI picture into the finished post: brand layout, headline, benefits, logo and contact footer.

The AI only paints the picture. Every word, the logo, the UAE flag and the contact details are drawn
here, so spelling, phone number, logo and flag are always exactly right.
"""

import io
import math
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .brand import Brand

W = 1080
SIZE = W  # square layouts
FONTS = Path(__file__).resolve().parent / "assets" / "fonts"
TEMPLATES = ("wave", "spotlight", "checklist", "skyline", "split", "circle", "cards", "frame")

ICON_PHONE = chr(0xE0CD)
ICON_WEB = chr(0xE894)
ICON_MAIL = chr(0xE158)
ICON_CHECK = chr(0xE5CA)

WHITE = (255, 255, 255, 255)

# Characters the poster font can draw (taken from the font files). Anything else is swapped or dropped,
# so the image never shows empty boxes (e.g. the AI often writes a non-breaking hyphen in "real‑time").
FONT_CHARS = set((FONTS / "montserrat-chars.txt").read_text(encoding="utf-8"))
SWAP = {"\u2010": "-", "\u2011": "-", "\u2012": "–", "\u2015": "—", "\u2212": "-", "\u202f": " ", "\u00a0": " ",
        "\u2009": " ", "\u2192": "–", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}


def clean(text) -> str:
    out = "".join(SWAP.get(ch, ch) for ch in str(text or ""))
    out = "".join(ch for ch in out if ch in FONT_CHARS or ch == " ")
    return " ".join(out.split())


def clean_content(content: dict) -> dict:
    c = dict(content)
    for k in ("headline_top", "headline_highlight", "subheadline", "body"):
        if c.get(k):
            c[k] = clean(c[k])
    for k in ("benefits", "bullets"):
        if c.get(k):
            c[k] = [x for x in (clean(v) for v in c[k]) if x]
    return c
UAE = {"red": "#FF0000", "green": "#00732F", "white": "#FFFFFF", "black": "#000000"}


# ---------- small helpers ----------

def rgb(hex_color: str, alpha: int | None = None) -> tuple:
    h = hex_color.lstrip("#")
    c = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return c if alpha is None else (*c, alpha)


@lru_cache(maxsize=64)
def font(weight: int | str, size: int) -> ImageFont.FreeTypeFont:
    name = "MaterialIcons-Regular.ttf" if weight == "icons" else f"Montserrat-{weight}.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def text_width(f: ImageFont.FreeTypeFont, text: str) -> float:
    return f.getlength(text)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if text_width(f, trial) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def fit(text: str, weight: int, max_width: float, max_size: int, min_size: int, max_lines: int):
    """Largest font size where the text fits in max_lines lines of max_width."""
    size = max_size
    while size > min_size:
        f = font(weight, size)
        lines = wrap(text, f, max_width)
        if len(lines) <= max_lines and all(text_width(f, ln) <= max_width for ln in lines):
            return f, lines
        size -= 2
    f = font(weight, min_size)
    return f, wrap(text, f, max_width)[:max_lines]


def gradient(size: tuple[int, int], stops: list[str], angle_deg: float = 0) -> Image.Image:
    """Multi-stop linear gradient. angle 0 = left→right, 90 = top→bottom."""
    w, h = size
    a = math.radians(angle_deg)
    xs, ys = np.meshgrid(np.arange(w), np.arange(h))
    t = xs * math.cos(a) + ys * math.sin(a)
    t = (t - t.min()) / max(t.max() - t.min(), 1)
    cols = np.array([rgb(s) for s in stops], dtype=float)
    pos = np.linspace(0, 1, len(stops))
    out = np.zeros((h, w, 3))
    for ch in range(3):
        out[..., ch] = np.interp(t, pos, cols[:, ch])
    return Image.fromarray(out.astype(np.uint8), "RGB")


def vertical_shade(size: tuple[int, int], color: str, stops: list[float], alphas: list[float]) -> Image.Image:
    """A colour layer whose transparency changes from top to bottom (for text readability over photos)."""
    w, h = size
    shade = np.zeros((h, w, 4), dtype=np.uint8)
    shade[..., :3] = rgb(color)
    alpha = np.interp(np.arange(h)[:, None], stops, alphas)
    shade[..., 3] = np.broadcast_to(alpha, (h, w)).astype(np.uint8)
    return Image.fromarray(shade, "RGBA")


def cover(img: Image.Image, w: int, h: int, focus: tuple[float, float] = (0.5, 0.5)) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    nw, nh = math.ceil(img.width * scale), math.ceil(img.height * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    if scale > 1.02:  # enlarged: bring back crispness
        img = img.filter(ImageFilter.UnsharpMask(radius=1.6, percent=70, threshold=2))
    left = int((nw - w) * focus[0])
    top = int((nh - h) * focus[1])
    return img.crop((left, top, left + w, top + h))


def trim_watermark(img: Image.Image) -> Image.Image:
    """Gemini puts a small sparkle mark in the bottom-right corner; cut that strip away."""
    w, h = img.size
    return img.crop((0, 0, int(w * 0.93), int(h * 0.93)))


def draw_text(layer: Image.Image, xy, text: str, f, fill, shadow: int = 0):
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((xy[0] + 2, xy[1] + 3), text, font=f, fill=(0, 0, 0, shadow))
        layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
    ImageDraw.Draw(layer).text(xy, text, font=f, fill=fill)


def draw_gradient_text(layer: Image.Image, xy, text: str, f, stops: list[str], shadow: int = 0):
    bbox = f.getbbox(text)
    w, h = bbox[2] + 4, bbox[3] + 4
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=f, fill=255)
    fill = gradient((w, h), stops, 0).convert("RGBA")
    fill.putalpha(mask)
    if shadow:
        sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).text((xy[0] + 2, xy[1] + 3), text, font=f, fill=(0, 0, 0, shadow))
        layer.alpha_composite(sh.filter(ImageFilter.GaussianBlur(6)))
    layer.alpha_composite(fill, (int(xy[0]), int(xy[1])))


def line_height(f) -> int:
    asc, desc = f.getmetrics()
    return asc + desc


def paste_logo(canvas: Image.Image, logo_path: Path, box: tuple[int, int, int, int]):
    """Scale the original logo file (never upscaled, never recoloured) into box, centred."""
    logo = Image.open(logo_path).convert("RGBA")
    x0, y0, x1, y1 = box
    scale = min((x1 - x0) / logo.width, (y1 - y0) / logo.height, 1.0)
    logo = logo.resize((round(logo.width * scale), round(logo.height * scale)), Image.LANCZOS)
    canvas.alpha_composite(logo, (x0 + (x1 - x0 - logo.width) // 2, y0 + (y1 - y0 - logo.height) // 2))


def rounded_card(canvas: Image.Image, box, radius: int, fill, shadow: int = 90):
    if shadow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        x0, y0, x1, y1 = box
        ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 10, x1, y1 + 10), radius, fill=(10, 15, 40, shadow))
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    ImageDraw.Draw(canvas).rounded_rectangle(box, radius, fill=fill)


def overlay_rect(canvas: Image.Image, box, fill):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rectangle(box, fill=fill)
    canvas.alpha_composite(layer)


def footer(canvas: Image.Image, brand: Brand, y: int, bg, fg=WHITE, icon_color=None):
    c = brand.company
    cw, ch = canvas.size
    overlay_rect(canvas, (0, y, cw, ch), bg)
    d = ImageDraw.Draw(canvas)
    items = [(ICON_PHONE, c["phone"]), (ICON_WEB, c["website"]), (ICON_MAIL, c["email"])]
    size = 27
    while True:
        f, fi = font(600, size), font("icons", size + 6)
        widths = [size + 14 + text_width(f, t) for _, t in items]
        gap = 56
        total = sum(widths) + gap * (len(items) - 1)
        if total <= cw - 80 or size <= 18:
            break
        size -= 1
    x = (cw - total) / 2
    cy = y + (ch - y) / 2
    icon_color = icon_color or fg
    for i, ((icon, label), w) in enumerate(zip(items, widths)):
        d.text((x, cy), icon, font=fi, fill=icon_color, anchor="lm")
        d.text((x + size + 14, cy), label, font=f, fill=fg, anchor="lm")
        x += w
        if i < len(items) - 1:
            d.line((x + gap / 2, cy - 16, x + gap / 2, cy + 16), fill=(*fg[:3], 110), width=2)
            x += gap


def benefit_strip(canvas: Image.Image, y0: int, y1: int, benefits: list[str], style: str, brand: Brand):
    """A full-width band with up to 3 short benefits, each with a check mark."""
    items = [b.strip() for b in benefits or [] if b and b.strip()][:3]
    if not items:
        return
    col = brand.colors
    cw = canvas.width
    if style == "gradient":
        band = gradient((cw, y1 - y0), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]], 0).convert("RGBA")
        canvas.alpha_composite(band, (0, y0))
        icon_bg, icon_fg = WHITE, rgb(col["brand_blue"], 255)
    else:  # glass, for dark photo layouts
        overlay_rect(canvas, (0, y0, cw, y1), (*rgb(col["deep_space"]), 205))
        line = gradient((cw, 3), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]], 0).convert("RGBA")
        canvas.alpha_composite(line, (0, y0))
        icon_bg, icon_fg = rgb(col["accent_yellow"], 255), rgb(col["navy"], 255)
    d = ImageDraw.Draw(canvas)
    n = len(items)
    col_w = (cw - 40) / n
    icon = 30
    size = 26
    while size > 16 and any(text_width(font(700, size), t) > col_w - icon - 30 for t in items):
        size -= 1
    f = font(700, size)
    cy = (y0 + y1) / 2 + 1
    for i, label in enumerate(items):
        gw = icon + 12 + text_width(f, label)
        x = 20 + i * col_w + (col_w - gw) / 2
        d.ellipse((x, cy - icon / 2, x + icon, cy + icon / 2), fill=icon_bg)
        d.text((x + icon / 2, cy), ICON_CHECK, font=font("icons", 24), fill=icon_fg, anchor="mm")
        d.text((x + icon + 12, cy), label, font=f, fill=WHITE, anchor="lm")
        if i < n - 1:
            sx = 20 + (i + 1) * col_w
            d.line((sx, cy - 18, sx, cy + 18), fill=(255, 255, 255, 90), width=2)


def smooth_curve(points: list[tuple[float, float]], steps: int = 40) -> list[tuple[float, float]]:
    """Catmull-Rom spline through points."""
    pts = [points[0], *points, points[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        for s in range(steps):
            t = s / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                       + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3)
                for k in range(2)
            ))
    out.append(points[-1])
    return out


def plus(d: ImageDraw.ImageDraw, x, y, s, fill, w=4):
    d.line((x - s, y, x + s, y), fill=fill, width=w)
    d.line((x, y - s, x, y + s), fill=fill, width=w)


def text_stack(canvas: Image.Image, x: int, top: int, bottom: int, rows: list[dict]) -> int:
    """Draw rows of text top-down, shrinking everything together until the block fits above `bottom`.

    Each row: text, weight, max_w, size, min, lines, fill (colour or list of gradient stops),
    and optional gap_after, shadow, dx, upper, center (centre each line within max_w).
    """
    rows = [r for r in rows if r.get("text")]
    for scale in (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.6):
        placed, y = [], top
        for r in rows:
            text = r["text"].upper() if r.get("upper", True) else r["text"]
            f, lines = fit(text, r["weight"], r["max_w"], int(r["size"] * scale), int(r["min"] * scale), r["lines"])
            for ln in lines:
                placed.append((y, ln, f, r))
                y += line_height(f) + r.get("line_gap", 0)
            y += r.get("gap_after", 0)
        if y <= bottom:
            break
    for py, ln, f, r in placed:
        dx = r.get("dx", 0) + ((r["max_w"] - text_width(f, ln)) / 2 if r.get("center") else 0)
        pos = (x + dx, py)
        if isinstance(r["fill"], list):
            draw_gradient_text(canvas, pos, ln, f, r["fill"], shadow=r.get("shadow", 0))
        else:
            draw_text(canvas, pos, ln, f, r["fill"], shadow=r.get("shadow", 0))
    return y


def uae_flag(width: int) -> Image.Image:
    """The UAE flag, drawn (not AI-made) so it is always correct, with a gentle wave."""
    s = 3
    fw, fh = width * s, width * s // 2
    flat = np.zeros((fh, fw, 3), dtype=float)
    red_w = fw // 4
    flat[:, :red_w] = rgb(UAE["red"])
    third = fh / 3
    for i, name in enumerate(("green", "white", "black")):
        flat[int(i * third):int((i + 1) * third), red_w:] = rgb(UAE[name])
    amp = fh * 0.07
    pad = int(amp * 2) + 2
    out = np.zeros((fh + 2 * pad, fw, 4), dtype=float)
    for x in range(fw):
        t = x / fw
        phase = 2 * math.pi * (t * 1.4)
        dy = int(round(amp * math.sin(phase) * t ** 0.7))
        shade = 1 + 0.22 * math.cos(phase) * t ** 0.7
        out[pad + dy:pad + dy + fh, x, :3] = np.clip(flat[:, x] * shade, 0, 255)
        out[pad + dy:pad + dy + fh, x, 3] = 255
    img = Image.fromarray(out.astype(np.uint8), "RGBA")
    return img.resize((width, round(img.height / s)), Image.LANCZOS)


def flag_on_pole(canvas: Image.Image, x: int, y: int, flag_w: int, pole_h: int):
    """Pole at x (top at y), flag flying to the right."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle((x - 4, y, x + 4, y + pole_h), 4, fill=(205, 210, 220, 255))
    d.line((x - 1, y + 4, x - 1, y + pole_h - 4), fill=(255, 255, 255, 200), width=2)
    d.ellipse((x - 9, y - 16, x + 9, y + 2), fill=(225, 190, 90, 255))
    flag = uae_flag(flag_w)
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sh.paste((0, 0, 0, 110), (x + 10, y + 16), flag.getchannel("A"))
    canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(8)))
    canvas.alpha_composite(layer)
    canvas.alpha_composite(flag, (x + 3, y + 6))


def circle_mask(d: int) -> Image.Image:
    """Smooth-edged round mask (drawn 4x larger, then scaled down)."""
    big = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(big).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
    return big.resize((d, d), Image.LANCZOS)


def badge(canvas: Image.Image, cx: float, cy: float, d: int, brand: Brand, label: str = ICON_CHECK,
          fill: list[str] | str | None = None, fg=WHITE):
    """A round badge (brand gradient by default) with a check mark or a short label such as "01"."""
    col = brand.colors
    stops = fill or [col["brand_cyan"], col["brand_blue"], col["brand_purple"]]
    disc = (gradient((d, d), stops, 45) if isinstance(stops, list) else Image.new("RGB", (d, d), rgb(stops))).convert("RGBA")
    disc.putalpha(circle_mask(d))
    canvas.alpha_composite(disc, (round(cx - d / 2), round(cy - d / 2)))
    f = font("icons", round(d * 0.62)) if label == ICON_CHECK else font(800, round(d * 0.4))
    ImageDraw.Draw(canvas).text((cx, cy), label, font=f, fill=fg, anchor="mm")


def soft_glow(canvas: Image.Image, box, color: str, alpha: int, blur: int):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse(box, fill=rgb(color, alpha))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def dot_grid(canvas: Image.Image, x: int, y: int, cols: int, rows: int, gap: int, r: float, fill):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i in range(cols):
        for j in range(rows):
            cx, cy = x + i * gap, y + j * gap
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    canvas.alpha_composite(layer)


def fit_one_line(items: list[str], weight: int, max_w: float, size: int, min_size: int) -> ImageFont.FreeTypeFont:
    """Largest font where every item fits on one line of max_w."""
    while size > min_size and any(text_width(font(weight, size), t) > max_w for t in items):
        size -= 1
    return font(weight, size)


# ---------- templates ----------

def tpl_wave(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    col = brand.colors
    canvas = gradient((SIZE, SIZE), [col["poster_blue"], "#3F68B0", col["lavender"]], 18).convert("RGBA")
    d = ImageDraw.Draw(canvas)

    # soft light in the top-right
    glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((620, -260, 1340, 420), fill=(255, 255, 255, 38))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))

    # photo below a flowing wave
    footer_y, strip_y = 985, 905
    wave = smooth_curve([(-10, 700), (300, 668), (560, 610), (740, 450), (900, 340), (1090, 300)])
    band = smooth_curve([(-10, 668), (300, 634), (560, 572), (740, 408), (900, 296), (1090, 250)])
    band_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(band_mask).polygon([*band, (SIZE + 10, SIZE), (-10, SIZE)], fill=255)
    canvas.paste(Image.new("RGBA", canvas.size, rgb(col["lavender"], 150)), (0, 0), band_mask.filter(ImageFilter.GaussianBlur(1)))
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon([*wave, (SIZE + 10, SIZE), (-10, SIZE)], fill=255)
    top = int(min(p[1] for p in wave))
    region = cover(photo, SIZE, strip_y - top, (0.55, 0.5)).convert("RGBA")
    photo_full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    photo_full.paste(region, (0, top))
    canvas.paste(photo_full, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.2)))

    # decorations
    for i in range(9):
        d.ellipse((64 + i * 30, 64, 64 + i * 30 + 10, 74), outline=rgb(col["accent_yellow"], 220), width=2)
    for i in range(4):
        x = 790 + i * 48
        d.polygon([(x, 196), (x + 26, 180), (x + 26, 212)], fill=rgb(col["brand_purple"], 235))
    plus(d, 560, 560, 13, (255, 255, 255, 150))
    plus(d, 110, 610, 14, (255, 255, 255, 140))
    plus(d, 960, 110, 10, (255, 255, 255, 150))

    text_stack(canvas, 64, 118, 575, [
        {"text": content["headline_top"], "weight": 800, "max_w": 600, "size": 70, "min": 40, "lines": 2,
         "fill": WHITE, "shadow": 60, "line_gap": -4, "gap_after": -4},
        {"text": content["headline_highlight"], "weight": 900, "max_w": 600, "size": 118, "min": 52, "lines": 1,
         "fill": rgb(col["accent_yellow"], 255), "shadow": 60, "dx": -3, "gap_after": 6},
        {"text": content.get("subheadline"), "weight": 600, "max_w": 560, "size": 34, "min": 22, "lines": 3,
         "fill": WHITE, "shadow": 50, "line_gap": 2},
    ])

    card = (650, 760, 1040, 880)
    rounded_card(canvas, card, 26, WHITE)
    paste_logo(canvas, brand.logo_color, (card[0] + 28, card[1] + 18, card[2] - 28, card[3] - 18))
    benefit_strip(canvas, strip_y, footer_y, content.get("benefits"), "gradient", brand)
    footer(canvas, brand, footer_y, rgb(col["navy"], 255))
    return canvas.convert("RGB")


def tpl_spotlight(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    col = brand.colors
    footer_y, strip_y = 985, 905
    canvas = cover(photo, SIZE, SIZE, (0.5, 0.6)).convert("RGBA")
    canvas.alpha_composite(vertical_shade(canvas.size, col["deep_space"], [0, 380, 600, 800, SIZE], [240, 215, 70, 120, 200]))
    d = ImageDraw.Draw(canvas)

    paste_logo(canvas, brand.logo_white, (56, 50, 356, 122))
    for i in range(6):
        d.ellipse((SIZE - 64 - i * 26 - 9, 80, SIZE - 64 - i * 26, 89), fill=rgb(col["accent_yellow"], 200 - i * 25))

    x = 60
    y = text_stack(canvas, x, 178, 450, [
        {"text": content["headline_top"], "weight": 800, "max_w": 900, "size": 64, "min": 36, "lines": 2,
         "fill": WHITE, "shadow": 90, "line_gap": -4},
        {"text": content["headline_highlight"], "weight": 900, "max_w": 900, "size": 112, "min": 52, "lines": 1,
         "fill": ["#3FD3F5", "#6FA8FF", "#B08CFF"], "shadow": 90, "dx": -2},
    ])
    bar = gradient((140, 7), [col["brand_cyan"], col["brand_purple"]]).convert("RGBA")
    canvas.alpha_composite(bar, (x, y + 8))
    text_stack(canvas, x, y + 34, 640, [
        {"text": content.get("subheadline"), "weight": 600, "max_w": 820, "size": 36, "min": 24, "lines": 3,
         "fill": (235, 240, 255, 255), "shadow": 90, "line_gap": 4, "upper": False},
    ])

    benefit_strip(canvas, strip_y, footer_y, content.get("benefits"), "glass", brand)
    footer(canvas, brand, footer_y, (*rgb(col["navy"]), 245), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


def tpl_checklist(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    col = brand.colors
    footer_y = 985
    canvas = gradient((SIZE, SIZE), ["#FFFFFF", "#EEF2FB"], 90).convert("RGBA")
    d = ImageDraw.Draw(canvas)

    # photo on the right, with a curved left edge and a brand-gradient ribbon
    edge = smooth_curve([(640, -10), (590, 260), (640, 560), (600, 820), (630, 1000)])
    ribbon = [(x - 16, y) for x, y in edge]
    rib_mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(rib_mask).polygon([*ribbon, (SIZE + 10, 1000), (SIZE + 10, -10)], fill=255)
    rib = gradient((SIZE, SIZE), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]], 90).convert("RGBA")
    canvas.paste(rib, (0, 0), rib_mask)
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon([*edge, (SIZE + 10, 1000), (SIZE + 10, -10)], fill=255)
    left = int(min(p[0] for p in edge))
    region = cover(photo, SIZE - left, footer_y, (0.5, 0.5)).convert("RGBA")
    full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    full.paste(region, (left, 0))
    canvas.paste(full, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.2)))

    paste_logo(canvas, brand.logo_color, (52, 52, 332, 120))

    x, max_w = 56, 488
    y = text_stack(canvas, x, 170, 400, [
        {"text": content["headline_top"], "weight": 800, "max_w": max_w, "size": 50, "min": 30, "lines": 2,
         "fill": rgb(col["navy"], 255), "line_gap": -4},
        {"text": content["headline_highlight"], "weight": 900, "max_w": max_w, "size": 78, "min": 40, "lines": 2,
         "fill": brand.profile["visual"]["logo_gradient"], "dx": -2, "line_gap": -2},
    ])
    y += 26

    bullets = [b for b in content.get("bullets") or content.get("benefits") or [] if b][:4]
    limit = 940
    fb, need = None, 0
    for size in range(32, 21, -1):
        fb = font(600, size)
        need = sum(max(min(len(wrap(b, fb, max_w - 64)), 2) * (line_height(fb) + 2), 44) + 30 for b in bullets)
        if y + need <= limit:
            break
    extra = min(max(limit - (y + need), 0) // (len(bullets) + 1), 34) if bullets else 0
    y += extra
    for b in bullets:
        lines = wrap(b, fb, max_w - 64)[:2]
        box = (x, y + 2, x + 40, y + 42)
        d.rounded_rectangle(box, 9, fill=rgb(col["accent_yellow"], 255))
        d.text(((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), ICON_CHECK, font=font("icons", 32), fill=rgb(col["navy"], 255), anchor="mm")
        ty = y + 22 - (len(lines) * (line_height(fb) + 2)) / 2 + (1 if len(lines) == 1 else 12)
        for ln in lines:
            d.text((x + 62, ty), ln, font=fb, fill=rgb(col["navy"], 255))
            ty += line_height(fb) + 2
        y += max(len(lines) * (line_height(fb) + 2), 44) + 30 + extra

    footer(canvas, brand, footer_y, rgb(col["navy"], 255))
    return canvas.convert("RGB")


def tpl_skyline(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    """Tall premium poster (1080×1350): night photo, UAE flag, big headline, benefit text."""
    col = brand.colors
    H = 1350
    footer_y, strip_y = H - 95, H - 175
    canvas = cover(photo, W, H, (0.5, 0.45)).convert("RGBA")
    canvas.alpha_composite(vertical_shade(canvas.size, col["deep_space"], [0, 430, 650, 880, 1040, H],
                                          [238, 205, 40, 50, 215, 245]))

    # a few stars in the dark sky
    stars = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(stars)
    rnd = random.Random(content.get("headline_highlight", ""))
    for _ in range(90):
        sx, sy, r = rnd.randint(0, W), rnd.randint(0, 520), rnd.choice((1, 1, 1.5, 2))
        sd.ellipse((sx - r, sy - r, sx + r, sy + r), fill=(255, 255, 255, rnd.randint(60, 190)))
    canvas.alpha_composite(stars)

    paste_logo(canvas, brand.logo_white, (56, 48, 476, 150))
    flag_on_pole(canvas, 868, 52, 160, 148)

    x = 60
    y = text_stack(canvas, x, 222, 560, [
        {"text": content["headline_top"], "weight": 800, "max_w": 940, "size": 58, "min": 34, "lines": 2,
         "fill": WHITE, "shadow": 100, "line_gap": -2},
        {"text": content["headline_highlight"], "weight": 900, "max_w": 960, "size": 100, "min": 50, "lines": 2,
         "fill": ["#3FD3F5", "#6FA8FF", "#B08CFF"], "shadow": 100, "dx": -2, "line_gap": -4},
    ])
    bar = gradient((160, 7), [col["brand_cyan"], col["brand_purple"]]).convert("RGBA")
    canvas.alpha_composite(bar, (x, y + 10))

    body = content.get("body") or content.get("subheadline")
    if body:
        for size in range(36, 25, -1):
            f = font(600, size)
            lines = wrap(body, f, 960)
            if len(lines) <= 3:
                break
        lines = lines[:3]
        ty = strip_y - 34 - len(lines) * (line_height(f) + 6)
        for ln in lines:
            draw_text(canvas, (x, ty), ln, f, (236, 241, 255, 255), shadow=120)
            ty += line_height(f) + 6

    benefit_strip(canvas, strip_y, footer_y, content.get("benefits"), "glass", brand)
    footer(canvas, brand, footer_y, (*rgb(col["navy"]), 245), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


def tpl_split(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    """Dark navy panel on the right with the headline and a numbered benefit list; photo on the left, diagonal edge."""
    col = brand.colors
    footer_y = 985
    canvas = gradient((SIZE, SIZE), [col["deep_space"], col["navy"], "#33287A"], 65).convert("RGBA")
    soft_glow(canvas, (620, 60, 1260, 700), col["brand_blue"], 80, 120)

    top_x, bottom_x = 500, 380
    ribbon = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(ribbon).polygon([(-10, -2), (top_x + 20, -2), (bottom_x + 20, footer_y), (-10, footer_y)], fill=255)
    canvas.paste(gradient((SIZE, SIZE), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]], 90).convert("RGBA"),
                 (0, 0), ribbon)
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon([(-10, -2), (top_x, -2), (bottom_x, footer_y), (-10, footer_y)], fill=255)
    full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    full.paste(cover(photo, top_x, footer_y, (0.5, 0.5)).convert("RGBA"), (0, 0))
    canvas.paste(full, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.2)))
    lines = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(lines).line((top_x + 58, 0, bottom_x + 58, footer_y), fill=(255, 255, 255, 34), width=2)
    canvas.alpha_composite(lines)

    x, max_w = 556, 472
    paste_logo(canvas, brand.logo_white, (x, 56, x + 300, 128))
    y = text_stack(canvas, x, 186, 480, [
        {"text": content["headline_top"], "weight": 800, "max_w": max_w, "size": 46, "min": 28, "lines": 2,
         "fill": WHITE, "line_gap": -4, "gap_after": 2},
        {"text": content["headline_highlight"], "weight": 900, "max_w": max_w, "size": 74, "min": 36, "lines": 2,
         "fill": rgb(col["accent_yellow"], 255), "dx": -2, "line_gap": -6},
    ])
    canvas.alpha_composite(gradient((120, 6), [col["brand_cyan"], col["brand_purple"]]).convert("RGBA"), (x, y + 14))
    text_stack(canvas, x, y + 42, 650, [
        {"text": content.get("subheadline"), "weight": 600, "max_w": max_w, "size": 28, "min": 20, "lines": 3,
         "fill": (214, 222, 250, 255), "line_gap": 4, "upper": False},
    ])

    items = [b for b in content.get("benefits") or [] if b][:3]
    row_h = 84
    top = footer_y - 34 - row_h * len(items)
    fb = fit_one_line(items, 700, max_w - 86, 30, 20)
    seps = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    for i, label in enumerate(items):
        cy = top + row_h * i + row_h / 2
        badge(canvas, x + 30, cy, 60, brand, f"{i + 1:02d}")
        ImageDraw.Draw(canvas).text((x + 86, cy), label, font=fb, fill=WHITE, anchor="lm")
        if i < len(items) - 1:
            ImageDraw.Draw(seps).line((x, top + row_h * (i + 1), x + max_w, top + row_h * (i + 1)),
                                      fill=(255, 255, 255, 40), width=2)
    canvas.alpha_composite(seps)
    footer(canvas, brand, footer_y, rgb(col["navy"], 255), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


def tpl_circle(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    """Bright blue-purple gradient, the photo in a big circle with orbit rings, benefits as rounded tags."""
    col = brand.colors
    footer_y = 985
    canvas = gradient((SIZE, SIZE), [col["navy"], col["brand_blue"], col["brand_purple"]], 35).convert("RGBA")
    cx, cy, r = 746, 652, 258
    soft_glow(canvas, (cx - r - 40, cy - r - 40, cx + r + 40, cy + r + 40), col["brand_cyan"], 110, 70)
    dot_grid(canvas, 868, 74, 7, 3, 24, 3, (255, 255, 255, 90))

    rings = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(rings)
    r1, r2 = r + 22, r + 56
    d.ellipse((cx - r1, cy - r1, cx + r1, cy + r1), outline=(255, 255, 255, 170), width=3)
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline=rgb(col["brand_cyan"], 150), width=2)
    d.arc((cx - r2, cy - r2, cx + r2, cy + r2), 285, 330, fill=rgb(col["accent_yellow"], 255), width=7)
    for angle, rad, size, fill in ((212, r2, 9, rgb(col["accent_yellow"], 255)), (28, r1, 7, WHITE),
                                   (118, r2, 6, (255, 255, 255, 200))):
        px, py = cx + rad * math.cos(math.radians(angle)), cy + rad * math.sin(math.radians(angle))
        d.ellipse((px - size, py - size, px + size, py + size), fill=fill)
    canvas.alpha_composite(rings)
    disc = cover(photo, 2 * r, 2 * r, (0.5, 0.5)).convert("RGBA")
    disc.putalpha(circle_mask(2 * r))
    canvas.alpha_composite(disc, (cx - r, cy - r))

    x = 60
    paste_logo(canvas, brand.logo_white, (x, 52, x + 300, 124))
    y = text_stack(canvas, x, 170, 345, [
        {"text": content["headline_top"], "weight": 800, "max_w": 900, "size": 50, "min": 30, "lines": 2,
         "fill": WHITE, "shadow": 60, "line_gap": -4},
        {"text": content["headline_highlight"], "weight": 900, "max_w": 900, "size": 84, "min": 40, "lines": 1,
         "fill": rgb(col["accent_yellow"], 255), "shadow": 60, "dx": -2},
    ])
    text_stack(canvas, x, y + 22, 640, [
        {"text": content.get("subheadline"), "weight": 600, "max_w": 380, "size": 28, "min": 20, "lines": 4,
         "fill": (228, 234, 255, 255), "shadow": 50, "line_gap": 4, "upper": False},
    ])

    items = [b for b in content.get("benefits") or [] if b][:3]
    tag_h, gap = 64, 18
    ft = fit_one_line(items, 700, 330, 26, 18)
    ty = footer_y - 40 - len(items) * tag_h - (len(items) - 1) * gap
    tags = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    td = ImageDraw.Draw(tags)
    for i, label in enumerate(items):
        y0 = ty + i * (tag_h + gap)
        w = 22 + 38 + 14 + text_width(ft, label) + 26
        td.rounded_rectangle((x, y0, x + w, y0 + tag_h), tag_h // 2, fill=(11, 15, 46, 120),
                             outline=(255, 255, 255, 120), width=2)
    canvas.alpha_composite(tags)
    for i, label in enumerate(items):
        mid = ty + i * (tag_h + gap) + tag_h / 2
        badge(canvas, x + 22 + 19, mid, 38, brand, fill=col["accent_yellow"], fg=rgb(col["navy"], 255))
        ImageDraw.Draw(canvas).text((x + 22 + 38 + 14, mid), label, font=ft, fill=WHITE, anchor="lm")
    footer(canvas, brand, footer_y, rgb(col["navy"], 255), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


def tpl_cards(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    """Tall post (1080×1350): photo on top, 3 benefit cards across its edge, centred headline below."""
    col = brand.colors
    H = 1350
    footer_y = H - 95
    canvas = gradient((W, H), ["#FFFFFF", "#EAF0FA"], 90).convert("RGBA")

    edge = smooth_curve([(-10, 700), (300, 730), (640, 712), (1090, 650)])
    ribbon = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(ribbon).polygon([(-10, -10), (W + 10, -10), *reversed([(px, py + 14) for px, py in edge])], fill=255)
    canvas.paste(gradient((W, H), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]], 0).convert("RGBA"),
                 (0, 0), ribbon)
    photo_h = int(max(py for _, py in edge)) + 2
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).polygon([(-10, -10), (W + 10, -10), *reversed(edge)], fill=255)
    full = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    full.paste(cover(photo, W, photo_h, (0.5, 0.5)).convert("RGBA"), (0, 0))
    full.alpha_composite(vertical_shade((W, photo_h), col["deep_space"], [0, 190, photo_h], [175, 0, 0]))
    canvas.paste(full, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.2)))

    paste_logo(canvas, brand.logo_white, (56, 48, 386, 128))
    dot_grid(canvas, W - 64 - 6 * 26, 84, 7, 1, 26, 4, rgb(col["accent_yellow"], 210))

    items = [b for b in content.get("benefits") or [] if b][:3] or ["Certified engineers"]
    n, margin, gap = len(items), 48, 24
    card_w = (W - 2 * margin - (n - 1) * gap) / n
    y0, y1 = 588, 808
    fc = None
    for size in range(32, 19, -1):
        fc = font(700, size)
        if all(len(wrap(t, fc, card_w - 40)) <= 2 for t in items):
            break
    for i, label in enumerate(items):
        x0 = margin + i * (card_w + gap)
        rounded_card(canvas, (round(x0), y0, round(x0 + card_w), y1), 26, WHITE, shadow=70)
        badge(canvas, x0 + card_w / 2, y0 + 64, 72, brand)
        lines = wrap(label, fc, card_w - 40)[:2]
        lh = line_height(fc)
        ty = y0 + 112 + (y1 - y0 - 112 - 18 - len(lines) * lh) / 2
        for ln in lines:
            ImageDraw.Draw(canvas).text((x0 + card_w / 2, ty + lh / 2), ln, font=fc, fill=rgb(col["navy"], 255), anchor="mm")
            ty += lh

    y = text_stack(canvas, 60, 862, 1110, [
        {"text": content["headline_top"], "weight": 800, "max_w": 960, "size": 50, "min": 30, "lines": 2,
         "fill": rgb(col["navy"], 255), "line_gap": -4, "center": True},
        {"text": content["headline_highlight"], "weight": 900, "max_w": 960, "size": 92, "min": 44, "lines": 1,
         "fill": brand.profile["visual"]["logo_gradient"], "center": True},
    ])
    canvas.alpha_composite(gradient((140, 6), [col["brand_cyan"], col["brand_purple"]]).convert("RGBA"), (W // 2 - 70, y + 12))
    text_stack(canvas, 90, y + 40, footer_y - 30, [
        {"text": content.get("subheadline"), "weight": 600, "max_w": 900, "size": 32, "min": 22, "lines": 2,
         "fill": (64, 78, 118, 255), "line_gap": 4, "upper": False, "center": True},
    ])
    footer(canvas, brand, footer_y, rgb(col["navy"], 255), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


def tpl_frame(photo: Image.Image, content: dict, brand: Brand) -> Image.Image:
    """Clean editorial look: full photo, a white card at the bottom with headline, subheadline and benefit tags."""
    col = brand.colors
    footer_y = 985
    canvas = cover(photo, SIZE, SIZE, (0.5, 0.3)).convert("RGBA")
    canvas.alpha_composite(vertical_shade(canvas.size, col["deep_space"], [0, 300, 520, footer_y, SIZE],
                                          [70, 0, 30, 120, 120]))
    left, right, bottom = 44, 1036, 952
    x, max_w = left + 52, right - left - 104
    items = [b for b in content.get("benefits") or [] if b][:3]
    ft, chip_h, chip_gap = None, 54, 14
    for size in range(24, 15, -1):
        ft = font(700, size)
        widths = [16 + 32 + 10 + text_width(ft, t) + 20 for t in items]
        if sum(widths) + chip_gap * (len(items) - 1) <= max_w:
            break
    chip_y = bottom - 42 - chip_h
    rows = [
        {"text": content["headline_top"], "weight": 800, "max_w": max_w - 280, "size": 42, "min": 26, "lines": 2,
         "fill": rgb(col["navy"], 255), "line_gap": -4, "gap_after": 4},
        {"text": content["headline_highlight"], "weight": 900, "max_w": max_w, "size": 80, "min": 40, "lines": 1,
         "fill": brand.profile["visual"]["logo_gradient"], "dx": -2, "gap_after": 8},
        {"text": content.get("subheadline"), "weight": 600, "max_w": max_w, "size": 27, "min": 20, "lines": 2,
         "fill": (70, 84, 120, 255), "line_gap": 4, "upper": False},
    ]
    # the card hugs its text: measure the block on a scratch layer, then draw the card that fits it
    block = text_stack(Image.new("RGBA", canvas.size), x, 0, chip_y - 22 - 470, rows)
    card = (left, max(470, chip_y - 22 - block - 44), right, bottom)
    rounded_card(canvas, card, 32, WHITE, shadow=120)
    canvas.alpha_composite(gradient((card[2] - card[0] - 64, 6), [col["brand_cyan"], col["brand_blue"], col["brand_purple"]]
                                    ).convert("RGBA"), (card[0] + 32, card[1]))
    paste_logo(canvas, brand.logo_color, (card[2] - 40 - 250, card[1] + 38, card[2] - 40, card[1] + 98))
    text_stack(canvas, x, card[1] + 44, chip_y - 22, rows)
    cx = x
    d = ImageDraw.Draw(canvas)
    for label, w in zip(items, widths):
        d.rounded_rectangle((cx, chip_y, cx + w, chip_y + chip_h), chip_h // 2, fill=(234, 241, 252, 255))
        badge(canvas, cx + 16 + 16, chip_y + chip_h / 2, 32, brand)
        d.text((cx + 16 + 32 + 10, chip_y + chip_h / 2), label, font=ft, fill=rgb(col["navy"], 255), anchor="lm")
        cx += w + chip_gap
    footer(canvas, brand, footer_y, rgb(col["navy"], 255), icon_color=rgb(col["accent_yellow"], 255))
    return canvas.convert("RGB")


RENDERERS = {"wave": tpl_wave, "spotlight": tpl_spotlight, "checklist": tpl_checklist, "skyline": tpl_skyline,
             "split": tpl_split, "circle": tpl_circle, "cards": tpl_cards, "frame": tpl_frame}


def thumbnail(jpeg_bytes: bytes, width: int = 480) -> bytes:
    """Small copy of a finished post for lists on the dashboard (about 30 KB instead of 300+ KB)."""
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    img.thumbnail((width, width * 2), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80, optimize=True, progressive=True)
    return buf.getvalue()


def compose(template: str, photo_bytes: bytes, content: dict, brand: Brand) -> bytes:
    """Build the finished post and return it as JPEG bytes."""
    if template not in RENDERERS:
        template = "wave"
    photo = trim_watermark(Image.open(io.BytesIO(photo_bytes)).convert("RGB"))
    img = RENDERERS[template](photo, clean_content(content), brand)
    buf = io.BytesIO()
    # progressive: on a slow connection the whole picture appears at once and sharpens, instead of top-to-bottom
    img.save(buf, "JPEG", quality=88, subsampling=0, optimize=True, progressive=True)
    return buf.getvalue()
