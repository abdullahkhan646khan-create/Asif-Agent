"""The "Showcase" post: a rich infographic poster that explains one service with several photos (1080×1350).

Content (written by the AI, see writer.make_showcase_brief): title + highlighted words, tagline, a banner line,
5 services with labels, "Why choose" points, 3 badges and a call to action. Photos: a big hero photo, two side
photos, 5 service photos and one "why" photo (made by Gemini as one hero picture and one photo sheet, see
imagegen / pipeline). Four layouts; every word is drawn here, so spelling, phone and website are always right.
"""

import io
import random

import numpy as np
from PIL import Image, ImageDraw

from . import composer as C
from .brand import Brand
from .faces import best_focus

W, H = 1080, 1350
WHITE = (255, 255, 255, 255)
LAYOUTS = {"sc_classic": "Showcase · Classic", "sc_light": "Showcase · Light", "sc_split": "Showcase · Split",
           "sc_grid": "Showcase · Grid"}
BADGE_ICON = {"engineer": 0xF0E2, "quality": 0xE8E8, "support": 0xE311, "speed": 0xEA0B, "security": 0xE897,
              "cloud": 0xE2BD, "ai": 0xF06C, "network": 0xE63E}
# where the hero photo goes in each layout, and where a face may be inside it (no banner or photo over it)
HERO = {
    "sc_classic": ((642, 310), [(0, 0, 642, 240)]),
    "sc_light": ((440, 470), [(0, 0, 440, 300), (230, 300, 440, 470)]),
    "sc_split": ((440, 630), [(0, 110, 440, 630)]),
    "sc_grid": ((652, 470), [(0, 0, 652, 360)]),
}


def tidy(value):
    """Clean every text for the poster font (special hyphens, emoji…)."""
    if isinstance(value, list):
        return [tidy(v) for v in value]
    if isinstance(value, dict):
        return {k: tidy(v) for k, v in value.items()}
    return C.clean(value) if isinstance(value, str) else value


def _hero(photo: Image.Image, layout: str, content: dict) -> Image.Image:
    (w, h), zones = HERO[layout]
    faces = C.visible_faces(content.get("_faces"), photo.width, photo.height)
    focus, _ = best_focus(faces, photo.width, photo.height, w, h, zones, (0.5, 0.4))
    return C.cover(photo, w, h, focus).convert("RGBA")


def face_fit(layout: str, content: dict, photo_size) -> float:
    pw, ph = int(photo_size[0] * 0.93), int(photo_size[1] * 0.93)
    faces = C.visible_faces(content.get("_faces"), pw, ph)
    (w, h), zones = HERO[layout]
    return best_focus(faces, pw, ph, w, h, zones, (0.5, 0.4))[1] if faces else 0.0


def _photo(canvas, img, box, radius=18, shadow=60, focus=(0.5, 0.45)):
    x0, y0, x1, y1 = (round(v) for v in box)
    C.paste_rounded(canvas, C.cover(img, x1 - x0, y1 - y0, focus).convert("RGBA"), (x0, y0), radius, shadow=shadow)


def _round_photo(canvas, img, cx, cy, d, ring=None):
    if ring:
        r = C.gradient((d + 12, d + 12), ring, 45).convert("RGBA")
        r.putalpha(C.circle_mask(d + 12))
        canvas.alpha_composite(r, (round(cx - d / 2 - 6), round(cy - d / 2 - 6)))
    canvas.paste(C.cover(img, d, d, (0.5, 0.4)).convert("RGBA"), (round(cx - d / 2), round(cy - d / 2)), C.circle_mask(d))


def _section(canvas, y, text, color, line, x0=60, x1=W - 60, center=True):
    f = C.fit_one_line([text], 800, x1 - x0 - 120, 30, 20)
    d = ImageDraw.Draw(canvas)
    tw = C.text_width(f, text)
    if center:
        cx = (x0 + x1) / 2
        d.text((cx, y), text, font=f, fill=color, anchor="mm")
        d.line((x0, y, cx - tw / 2 - 22, y), fill=line, width=2)
        d.line((cx + tw / 2 + 22, y, x1, y), fill=line, width=2)
    else:
        d.text((x0, y), text, font=f, fill=color, anchor="lm")
        d.line((x0 + tw + 18, y, x1, y), fill=line, width=2)


def _title(canvas, c, x, top, max_w, color, hi_fill, center=True, size=66, bottom=None):
    return C.text_stack(canvas, x, top, bottom or top + 220, [
        {"text": c["title"], "weight": 900, "max_w": max_w, "size": size, "min": 34, "lines": 2, "fill": color,
         "upper": False, "center": center, "line_gap": -6},
        {"text": c.get("title_highlight"), "weight": 900, "max_w": max_w, "size": size, "min": 34, "lines": 1,
         "fill": hi_fill, "upper": False, "center": center},
    ])


def _checks(canvas, items, x, y, row_h, max_w, color, brand, size=23):
    f = C.fit_one_line(items, 700, max_w - 44, size, 14)
    d = ImageDraw.Draw(canvas)
    for i, t in enumerate(items):
        cy = y + i * row_h + row_h / 2
        C.badge(canvas, x + 15, cy, 30, brand, label="")
        d.text((x + 15, cy), C.ICON_CHECK, font=C.font("icons", 22), fill=WHITE, anchor="mm")
        d.text((x + 42, cy), t, font=f, fill=color, anchor="lm")


def _badges(canvas, badges, y, h, brand, style="navy"):
    col = brand.colors
    bw, gap = (W - 96 - 2 * 16) / 3, 16
    labels = [b["label"] for b in badges[:3]]
    f = C.fit_one_line(labels, 800, bw - 100, 21, 14)
    for i, b in enumerate(badges[:3]):
        x0 = 48 + i * (bw + gap)
        if style == "navy":
            card = C.gradient((round(bw), h), [col["navy"], col["brand_blue"]], 0).convert("RGBA")
            C.paste_rounded(canvas, card, (round(x0), y), 16, shadow=60)
            fg, disc, ic = WHITE, [col["accent_yellow"], "#FFE27D"], C.rgb(col["navy"], 255)
        elif style == "light":
            C.rounded_card(canvas, (round(x0), y, round(x0 + bw), y + h), 16, WHITE, shadow=40)
            fg, disc, ic = C.rgb(col["navy"], 255), None, WHITE
        else:  # neon
            C.pill(canvas, (round(x0), y, round(x0 + bw), y + h), (255, 255, 255, 14), (0, 200, 255, 140))
            fg, disc, ic = WHITE, [col["brand_cyan"], col["brand_purple"]], WHITE
        C.badge(canvas, x0 + 44, y + h / 2, 50, brand, label="", fill=disc)
        d = ImageDraw.Draw(canvas)
        d.text((x0 + 44, y + h / 2), chr(BADGE_ICON.get(b.get("icon"), 0xE86C)), font=C.font("icons", 30), fill=ic, anchor="mm")
        d.text((x0 + 82, y + h / 2), b["label"], font=f, fill=fg, anchor="lm")


def _cta_footer(canvas, c, brand, cta_color, y=1282):
    col = brand.colors
    f = C.fit_one_line([c["cta"]], 800, W - 120, 25, 18)
    ImageDraw.Draw(canvas).text((W / 2, y), c["cta"], font=f, fill=cta_color, anchor="mm")
    C.footer(canvas, brand, H - 52, C.rgb(col["navy"], 255), icon_color=C.rgb(col["accent_yellow"], 255))


def _banner_text(canvas, c, y, color=WHITE, hi=None, size=30):
    hi = hi or (248, 208, 74, 255)
    parts = [(c.get("banner_top", "") + " ", C.font(800, size), color), (c.get("banner_highlight", ""), C.font(900, size), hi)]
    while sum(C.text_width(f, t) for t, f, _ in parts) > W - 220 and size > 18:
        size -= 1
        parts = [(t, C.font(800 if i == 0 else 900, size), fill) for i, (t, _, fill) in enumerate(parts)]
    tw = sum(C.text_width(f, t) for t, f, _ in parts)
    x = (W - tw) / 2
    d = ImageDraw.Draw(canvas)
    for t, f, fill in parts:
        d.text((x, y), t, font=f, fill=fill, anchor="lm")
        x += C.text_width(f, t)


# ---------------------------------------------------------------- 1. CLASSIC
def sc_classic(p: dict, c: dict, brand: Brand) -> Image.Image:
    """Navy title band with the city behind it, a hero collage with a banner, 5 photo tiles, why + photo, badges."""
    col = brand.colors
    canvas = C.gradient((W, H), ["#F4F7FC", "#E6EDF8"], 90).convert("RGBA")
    band = C.gradient((W, 560), [col["deep_space"], col["navy"], "#2B3F8F"], 30).convert("RGBA")
    city = C.cover(p["side2"], W, 560, (0.5, 0.4)).convert("RGBA")
    city.putalpha(60)
    band.alpha_composite(city)
    canvas.alpha_composite(band)
    C.paste_logo(canvas, brand.logo_white, (48, 34, 48 + 230, 34 + 54))
    if C.service_tag(c):
        C.chip(canvas, W - 48 - C.text_width(C.font(700, 17), C.service_tag(c)) - 36, 40, C.service_tag(c), WHITE,
               (255, 255, 255, 36), size=17, h=40, pad=18, border=(255, 255, 255, 80))
    _title(canvas, c, 48, 104, W - 96, WHITE, ["#FFE27D", "#F8D04A"], size=66, bottom=258)
    tag_f = C.fit_one_line([c["tagline"]], 600, W - 220, 24, 17)
    tw = C.text_width(tag_f, c["tagline"]) + 60
    C.pill(canvas, ((W - tw) / 2, 262, (W + tw) / 2, 306), (255, 255, 255, 30), (255, 255, 255, 70))
    ImageDraw.Draw(canvas).text((W / 2, 284), c["tagline"], font=tag_f, fill=(230, 236, 255, 255), anchor="mm")
    C.paste_rounded(canvas, _hero(p["hero"], "sc_classic", c), (48, 330), 22, shadow=120)
    _photo(canvas, p["side1"], (708, 330, 1032, 480), 22, shadow=120)
    _photo(canvas, p["why"], (708, 490, 1032, 640), 22, shadow=120)
    C.frosted(canvas, (90, 584, 990, 676), radius=22, tint=(10, 20, 60, 175), blur=20)
    _banner_text(canvas, c, 616)
    sub = C.fit_one_line([c.get("banner_bottom", "")], 600, 820, 24, 16)
    ImageDraw.Draw(canvas).text((W / 2, 652), c.get("banner_bottom", ""), font=sub, fill=(214, 222, 250, 255), anchor="mm")
    _section(canvas, 718, c["section_title"], C.rgb(col["navy"], 255), (190, 202, 225, 255))
    n, gap, top = 5, 14, 748
    tw_ = (W - 96 - gap * (n - 1)) / n
    for i, item in enumerate(c["items"][:5]):
        x0 = 48 + i * (tw_ + gap)
        C.rounded_card(canvas, (round(x0), top, round(x0 + tw_), top + 184), 16, WHITE, shadow=50)
        _photo(canvas, p["tiles"][i], (x0 + 6, top + 6, x0 + tw_ - 6, top + 118), 12, shadow=0)
        lines = C.wrap(item["label"], C.font(700, 17), tw_ - 16)[:2]
        for k, ln in enumerate(lines):
            ImageDraw.Draw(canvas).text((x0 + tw_ / 2, top + 151 + k * 21 - (len(lines) - 1) * 10), ln,
                                        font=C.font(700, 17), fill=C.rgb(col["navy"], 255), anchor="mm")
    _section(canvas, 966, c["why_title"], C.rgb(col["navy"], 255), (190, 202, 225, 255))
    C.rounded_card(canvas, (48, 994, W - 48, 1166), 20, WHITE, shadow=50)
    _photo(canvas, p["tiles"][0] if len(p["tiles"]) < 6 else p["tiles"][5], (W - 48 - 360, 1004, W - 58, 1156), 14, shadow=0)
    _checks(canvas, c["why"][:4], 78, 1006, 39, 540, C.rgb(col["navy"], 255), brand)
    _badges(canvas, c["badges"], 1182, 74, brand, "navy")
    _cta_footer(canvas, c, brand, C.rgb(col["navy"], 255))
    return canvas.convert("RGB")


# ---------------------------------------------------------------- 2. LIGHT
def sc_light(p: dict, c: dict, brand: Brand) -> Image.Image:
    """Light magazine look: title left, a hero photo with two overlapping photos right, round service photos."""
    col = brand.colors
    canvas = C.gradient((W, H), ["#FFFFFF", "#EEF3FB"], 90).convert("RGBA")
    C.soft_glow(canvas, (600, -200, 1300, 500), col["brand_cyan"], 45, 140)
    C.paste_logo(canvas, brand.logo_color, (56, 46, 56 + 260, 46 + 62))
    x, mw = 60, 500
    top = 150
    if C.service_tag(c):
        C.chip(canvas, x, top, C.service_tag(c), C.rgb(col["brand_blue"], 255), (226, 236, 252, 255), size=17, h=40, pad=14)
        top += 64
    y = _title(canvas, c, x, top, mw, C.rgb(col["navy"], 255), brand.profile["visual"]["logo_gradient"], center=False,
               size=58, bottom=top + 250)
    ImageDraw.Draw(canvas).line((x, y + 14, x + 90, y + 14), fill=C.rgb(col["accent_yellow"], 255), width=5)
    y = C.text_stack(canvas, x, y + 34, 560, [
        {"text": c["tagline"], "weight": 600, "max_w": mw, "size": 26, "min": 18, "lines": 2, "fill": (70, 84, 120, 255),
         "upper": False, "line_gap": 4, "gap_after": 14},
        {"text": f"{c.get('banner_top', '')} {c.get('banner_highlight', '')}".strip(), "weight": 800, "max_w": mw,
         "size": 26, "min": 18, "lines": 2, "fill": C.rgb(col["brand_blue"], 255), "upper": False},
    ])
    C.paste_rounded(canvas, _hero(p["hero"], "sc_light", c), (592, 120), 30, shadow=110)
    C.rounded_card(canvas, (556, 466, 784, 624), 24, WHITE, shadow=110)
    _photo(canvas, p["side1"], (562, 472, 778, 618), 20, shadow=0)
    _section(canvas, 690, c["section_title"], C.rgb(col["navy"], 255), (200, 212, 232, 255))
    n = 5
    cw = (W - 96) / n
    f = C.font(700, 17)
    for i, item in enumerate(c["items"][:5]):
        cx = 48 + cw * i + cw / 2
        _round_photo(canvas, p["tiles"][i], cx, 800, 138, ring=[col["brand_cyan"], col["brand_blue"], col["brand_purple"]])
        lines = C.wrap(item["label"], f, cw - 12)[:2]
        for k, ln in enumerate(lines):
            ImageDraw.Draw(canvas).text((cx, 900 + k * 21), ln, font=f, fill=C.rgb(col["navy"], 255), anchor="mm")
    C.rounded_card(canvas, (48, 948, W - 48, 1162), 22, WHITE, shadow=50)
    _photo(canvas, p["why"], (60, 960, 400, 1150), 16, shadow=0)
    ImageDraw.Draw(canvas).text((428, 990), c["why_title"], font=C.fit_one_line([c["why_title"]], 800, 580, 28, 18),
                                fill=C.rgb(col["navy"], 255), anchor="lm")
    _checks(canvas, c["why"][:4], 428, 1012, 36, W - 48 - 438, (60, 72, 104, 255), brand, size=21)
    _badges(canvas, c["badges"], 1180, 72, brand, "light")
    _cta_footer(canvas, c, brand, C.rgb(col["navy"], 255))
    return canvas.convert("RGB")


# ---------------------------------------------------------------- 3. SPLIT
def sc_split(p: dict, c: dict, brand: Brand) -> Image.Image:
    """A photo column on the left (hero + two photos), everything else as a clean list on the right."""
    col = brand.colors
    canvas = C.gradient((W, H), [col["deep_space"], col["navy"], "#2A2470"], 70).convert("RGBA")
    C.soft_glow(canvas, (520, -200, 1300, 500), col["brand_cyan"], 60, 140)
    C.paste_rounded(canvas, _hero(p["hero"], "sc_split", c), (40, 40), 26, shadow=120)
    _photo(canvas, p["side1"], (40, 684, 480, 954), 26, shadow=120)
    _photo(canvas, p["why"], (40, 968, 480, 1236), 26, shadow=120)
    logo_card = (60, 60, 60 + 250, 60 + 72)
    C.frosted(canvas, logo_card, radius=18, tint=(8, 16, 48, 150), blur=14)
    C.paste_logo(canvas, brand.logo_white, (logo_card[0] + 18, logo_card[1] + 12, logo_card[2] - 18, logo_card[3] - 12))
    x, mw = 520, W - 520 - 48
    top = 60
    if C.service_tag(c):
        C.chip(canvas, x, top, C.service_tag(c), C.rgb(col["navy"], 255), C.rgb(col["accent_yellow"], 255), size=16, h=38, pad=14)
        top += 58
    y = _title(canvas, c, x, top, mw, WHITE, ["#3FD3F5", "#6FA8FF", "#B08CFF"], center=False, size=54, bottom=top + 230)
    y = C.text_stack(canvas, x, y + 12, 470, [
        {"text": c["tagline"], "weight": 600, "max_w": mw, "size": 23, "min": 17, "lines": 2, "fill": (205, 214, 245, 255),
         "upper": False, "line_gap": 4},
    ])
    d = ImageDraw.Draw(canvas)
    sy = max(y + 30, 430)
    d.text((x, sy), c["section_title"], font=C.fit_one_line([c["section_title"]], 800, mw, 24, 16),
           fill=C.rgb(col["accent_yellow"], 255), anchor="lm")
    f = C.fit_one_line([i["label"] for i in c["items"][:5]], 700, mw - 90, 22, 15)
    for i, item in enumerate(c["items"][:5]):
        ry = sy + 30 + i * 76
        _photo(canvas, p["tiles"][i], (x, ry, x + 62, ry + 62), 14, shadow=0)
        d.text((x + 80, ry + 31), item["label"], font=f, fill=WHITE, anchor="lm")
        if i < 4:
            d.line((x + 80, ry + 69, x + mw, ry + 69), fill=(255, 255, 255, 30), width=1)
    wy = sy + 30 + 5 * 76 + 18
    d.text((x, wy), c["why_title"], font=C.fit_one_line([c["why_title"]], 800, mw, 24, 16),
           fill=C.rgb(col["accent_yellow"], 255), anchor="lm")
    _checks(canvas, c["why"][:4], x, wy + 18, 38, mw, (230, 236, 255, 255), brand, size=20)
    by = wy + 18 + 4 * 38 + 18
    labels = [b["label"] for b in c["badges"][:3]]
    fb = C.fit_one_line(labels, 700, mw - 70, 19, 13)
    for i, b in enumerate(c["badges"][:3]):
        yy = by + i * 52
        C.pill(canvas, (x, yy, x + mw, yy + 44), (255, 255, 255, 18), (255, 255, 255, 50))
        ImageDraw.Draw(canvas).text((x + 26, yy + 22), chr(BADGE_ICON.get(b.get("icon"), 0xE86C)), font=C.font("icons", 24),
                                    fill=C.rgb(col["accent_yellow"], 255), anchor="mm")
        ImageDraw.Draw(canvas).text((x + 50, yy + 22), b["label"], font=fb, fill=WHITE, anchor="lm")
    _cta_footer(canvas, c, brand, WHITE, y=1268)
    return canvas.convert("RGB")


# ---------------------------------------------------------------- 4. GRID
def sc_grid(p: dict, c: dict, brand: Brand) -> Image.Image:
    """Dark tech mosaic: a big hero tile and 5 service tiles with their labels on the photos."""
    col = brand.colors
    canvas = C.gradient((W, H), ["#04071A", "#0B1540", "#1B1450"], 60).convert("RGBA")
    lines = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lines)
    rnd = random.Random(c.get("title", ""))
    for _ in range(14):
        x0, y0 = rnd.randint(0, W), rnd.randint(0, H)
        x1 = x0 + rnd.choice((-1, 1)) * rnd.randint(80, 240)
        ld.line((x0, y0, x1, y0, x1, y0 + rnd.randint(50, 180)), fill=(0, 200, 255, 40), width=2)
        ld.ellipse((x1 - 4, y0 - 4, x1 + 4, y0 + 4), fill=(0, 220, 255, 90))
    canvas.alpha_composite(lines)
    C.paste_logo(canvas, brand.logo_white, (48, 36, 48 + 230, 36 + 54))
    if C.service_tag(c):
        C.chip(canvas, W - 48 - C.text_width(C.font(700, 17), C.service_tag(c)) - 36, 42, C.service_tag(c), WHITE,
               (255, 255, 255, 18), size=17, h=40, pad=18, border=(0, 200, 255, 140))
    y = _title(canvas, c, 48, 110, W - 96, WHITE, ["#3FD3F5", "#6FA8FF", "#B08CFF"], size=62, bottom=260)
    tag_f = C.fit_one_line([c["tagline"]], 600, W - 160, 24, 17)
    ImageDraw.Draw(canvas).text((W / 2, y + 22), c["tagline"], font=tag_f, fill=(205, 214, 245, 255), anchor="mm")
    gx, gy, gap = 48, max(y + 56, 330), 12
    cw, ch = (W - 96 - 2 * gap) / 3, 229
    C.paste_rounded(canvas, _hero(p["hero"], "sc_grid", c), (gx, gy), 20, shadow=100)
    hb = (gx, gy + 2 * ch + gap - 92, gx + 2 * cw + gap, gy + 2 * ch + gap)
    C.frosted(canvas, (hb[0] + 12, hb[1], hb[2] - 12, hb[3] - 12), radius=16, tint=(8, 16, 48, 160), blur=16)
    hl = C.fit_one_line([f"{c.get('banner_top', '')} {c.get('banner_highlight', '')}"], 800, hb[2] - hb[0] - 60, 24, 15)
    ImageDraw.Draw(canvas).text(((hb[0] + hb[2]) / 2, hb[1] + 40), f"{c.get('banner_top', '')} {c.get('banner_highlight', '')}",
                                font=hl, fill=WHITE, anchor="mm")
    cells = [(2, 0), (2, 1), (0, 2), (1, 2), (2, 2)]
    fl = C.fit_one_line([i["label"] for i in c["items"][:5]], 700, cw - 30, 20, 13)
    for i, (col_i, row_i) in enumerate(cells):
        x0, y0 = gx + col_i * (cw + gap), gy + row_i * (ch + gap)
        tile = C.cover(p["tiles"][i], round(cw), ch, (0.5, 0.4)).convert("RGBA")
        tile.alpha_composite(C.vertical_shade(tile.size, "#050A24", [0, ch * 0.5, ch], [0, 60, 230]))
        C.paste_rounded(canvas, tile, (round(x0), round(y0)), 20, shadow=80)
        ImageDraw.Draw(canvas).text((x0 + 18, y0 + ch - 26), c["items"][i]["label"], font=fl, fill=WHITE, anchor="lm")
        ImageDraw.Draw(canvas).line((x0 + 18, y0 + ch - 48, x0 + 58, y0 + ch - 48), fill=(90, 230, 255, 255), width=4)
    wy = gy + 3 * ch + 2 * gap + 22
    half = (W - 96) / 2
    _checks(canvas, c["why"][:2], 48, wy, 36, half - 10, (230, 236, 255, 255), brand, size=20)
    _checks(canvas, c["why"][2:4], 48 + half + 10, wy, 36, half - 10, (230, 236, 255, 255), brand, size=20)
    _badges(canvas, c["badges"], wy + 84, 66, brand, "neon")
    _cta_footer(canvas, c, brand, WHITE)
    return canvas.convert("RGB")


RENDERERS = {"sc_classic": sc_classic, "sc_light": sc_light, "sc_split": sc_split, "sc_grid": sc_grid}


def compose_showcase(layout: str, photos: dict, content: dict, brand: Brand) -> bytes:
    """photos: {"hero", "side1", "side2", "why": bytes, "tiles": [bytes x5]} → the finished poster (JPEG)."""
    load = lambda b: C.trim_watermark(Image.open(io.BytesIO(b)).convert("RGB"))  # noqa: E731
    imgs = {k: load(v) for k, v in photos.items() if k != "tiles"}
    imgs["tiles"] = [load(b) for b in photos["tiles"]]
    img = RENDERERS.get(layout, sc_classic)(imgs, tidy(content), brand)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88, subsampling=0, optimize=True, progressive=True)
    return buf.getvalue()


# ---------- the photo sheet: one Gemini picture with 9 photos, cut into tiles ----------

def _gutters(profile: np.ndarray, size: int) -> list[tuple[int, int]] | None:
    """Two separator bands (uniform rows/columns) near 1/3 and 2/3 of the picture, or None."""
    found = []
    for lo, hi in ((0.22, 0.44), (0.56, 0.78)):
        idx = [i for i in range(int(size * lo), int(size * hi)) if profile[i]]
        if not idx:
            return None
        runs, start = [], idx[0]
        for a, b in zip(idx, idx[1:] + [None]):
            if b != a + 1:
                runs.append((start, a))
                start = b
        found.append(max(runs, key=lambda r: r[1] - r[0]))
    return found


def cut_sheet(sheet_bytes: bytes) -> list[bytes] | None:
    """Cut a 3×3 photo sheet into its 9 photos (JPEG). None if the separators can't be found (then the caller
    uses other photos)."""
    img = Image.open(io.BytesIO(sheet_bytes)).convert("RGB")
    g = np.asarray(img.convert("L"), dtype=float)

    def uniform(a, axis):  # a nearly uniform line (white, black or one colour) across the picture
        return (a.std(axis=axis) < 14) | ((a > 228).mean(axis=axis) > 0.9)
    # the outer border first
    cols, rows = uniform(g, 0), uniform(g, 1)
    xs_in, ys_in = np.where(~cols)[0], np.where(~rows)[0]
    if not len(xs_in) or not len(ys_in):
        return None
    x_a, x_b, y_a, y_b = xs_in[0], xs_in[-1] + 1, ys_in[0], ys_in[-1] + 1
    img, g = img.crop((x_a, y_a, x_b, y_b)), g[y_a:y_b, x_a:x_b]
    h, w = g.shape
    gc, gr = _gutters(list(uniform(g, 0)), w), _gutters(list(uniform(g, 1)), h)
    if not gc or not gr:
        return None
    xs = [(0, gc[0][0]), (gc[0][1] + 1, gc[1][0]), (gc[1][1] + 1, w)]
    ys = [(0, gr[0][0]), (gr[0][1] + 1, gr[1][0]), (gr[1][1] + 1, h)]
    if max(b - a for a, b in xs) > 1.6 * min(b - a for a, b in xs) or max(b - a for a, b in ys) > 1.6 * min(b - a for a, b in ys):
        return None  # cells of very different sizes: not a clean 3×3 sheet
    out = []
    for y0, y1 in ys:
        for x0, x1 in xs:
            cell = img.crop((x0 + 4, y0 + 4, x1 - 4, y1 - 4))
            if cell.width < 80 or cell.height < 80 or np.asarray(cell.convert("L")).std() < 12:
                return None  # an empty or tiny cell
            buf = io.BytesIO()
            cell.save(buf, "JPEG", quality=92)
            out.append(buf.getvalue())
    return out
