"""The client's own photos and videos: the checks, getting pictures ready for every network, and Buffer's limits.

The limits are Buffer's own (support.buffer.com: "Sharing videos through Buffer", "Ideal image sizes and formats
for your Buffer posts", "Troubleshooting video uploads in Buffer"), checked October 2026:
- pictures: Buffer takes files under 10 MB (8 MB for Facebook and Instagram, 1 MB for Bluesky). Instagram needs a
  shape between 4:5 (tall) and 1.91:1 (wide), and crops a carousel to the shape of its first picture.
- videos: one per post, MP4 or MOV (H.264 is the safest). Instagram publishes every video as a Reel (3 s to 15 min,
  up to 300 MB); TikTok 3 s to 10 min; X up to 140 s; Mastodon 40 MB, Bluesky 100 MB, the others 1 GB;
  Google Business Profile takes no videos.
The storage (Supabase free plan) keeps files up to 50 MB, so that is the video limit here (MAX_VIDEO_MB).
"""

import asyncio
import io
import json
import re
import uuid

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .composer import thumbnail
from .config import get_settings
from .store import get_store

MB = 1024 * 1024
KIND_NAMES = {"image": "photo", "carousel": "carousel", "video": "video"}
MAX_IMAGE_MB = 20          # what the client may pick; every picture is made much smaller here
MAX_IMAGES = 10            # Instagram's carousel limit
MIN_SIDE = 320             # Instagram and Facebook need pictures at least 320 px wide
TALLEST, WIDEST = 0.8, 1.91  # Instagram: from 4:5 to 1.91:1 (width / height)
MIN_SECONDS, MAX_SECONDS = 3, 15 * 60  # Instagram Reels
MIN_VIDEO_SIDE = 360       # TikTok's minimum
VIDEO_TYPES = {"mp4": "video/mp4", "mov": "video/quicktime"}

# per Buffer channel type: how many pictures one post can carry there (the first ones are used)
IMAGES_PER_POST = {"instagram": 10, "facebook": 10, "linkedin": 20, "twitter": 4, "threads": 4, "tiktok": 35,
                   "pinterest": 1, "googlebusiness": 1, "bluesky": 4, "mastodon": 4, "substack": 6}
VIDEO_MB = {"instagram": 300, "mastodon": 40, "bluesky": 100}  # every other network: 1 GB
VIDEO_SECONDS = {"instagram": (3, 900), "tiktok": (3, 600), "twitter": (0.5, 140)}
NO_VIDEO = {"googlebusiness": "Google Business Profile doesn't take videos"}

VIDEO_PATH = re.compile(r"^uploads/([0-9a-f]{32})/video\.(mp4|mov)$")
CONTAINER_BOXES = (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")  # how an MP4/MOV file starts


class UploadError(ValueError):
    """Something the client can fix (wrong file, too big, too short): shown to them as it is."""


def max_video_bytes() -> int:
    return get_settings().max_video_mb * MB


def secs(s: float) -> str:
    """140 → '2 min 20 s', 3 → '3 s', 900 → '15 min'."""
    s = round(s)
    m, rest = divmod(s, 60)
    if not m:
        return f"{rest} s"
    return f"{m} min" + (f" {rest} s" if rest else "")


def clock(s: float) -> str:
    """42 → '0:42'."""
    m, rest = divmod(round(s or 0), 60)
    return f"{m}:{rest:02d}"


def media_label(media: dict) -> str:
    """'photo', 'carousel of 5 photos' or 'video (0:42)'."""
    if media.get("kind") == "video":
        return f"video ({clock((media.get('video') or {}).get('duration'))})"
    if media.get("kind") == "carousel":
        return f"carousel of {len(media.get('items') or [])} photos"
    return "photo"


def video_problem(service: str, video: dict) -> str | None:
    """Why this video can't go to this kind of channel (it is skipped there), or None."""
    if service in NO_VIDEO:
        return NO_VIDEO[service]
    mb = VIDEO_MB.get(service, 1024)
    if (video.get("size") or 0) > mb * MB:
        return f"videos there can be at most {mb} MB"
    lo, hi = VIDEO_SECONDS.get(service, (0, 4 * 3600))
    d = float(video.get("duration") or 0)
    if d and not lo <= d <= hi:
        return f"videos there must be {secs(lo)} to {secs(hi)} long"
    return None


def page_rules(channels: list[dict]) -> dict:
    """The limits for the Upload page, including what each connected channel accepts."""
    from .publisher import SERVICES
    return {
        "max_image_mb": MAX_IMAGE_MB, "max_images": MAX_IMAGES, "min_side": MIN_SIDE, "tallest": TALLEST,
        "widest": WIDEST, "max_video_mb": get_settings().max_video_mb, "min_seconds": MIN_SECONDS,
        "max_seconds": MAX_SECONDS, "min_video_side": MIN_VIDEO_SIDE,
        "channels": [{"service": c["service"], "label": SERVICES.get(c["service"], c["service"]), "name": c["name"],
                      "images": IMAGES_PER_POST.get(c["service"], 10), "video_mb": VIDEO_MB.get(c["service"], 1024),
                      "video_seconds": VIDEO_SECONDS.get(c["service"]), "no_video": NO_VIDEO.get(c["service"])}
                     for c in channels],
    }


# ---------- pictures ----------

def _rgb(img: Image.Image) -> Image.Image:
    """Upright (phone pictures carry their rotation separately) and plain RGB (transparent parts become white)."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def fit_shape(img: Image.Image, ratio: float) -> Image.Image:
    """Give the picture the shape `ratio` (width / height) by placing it on a soft, blurred copy of itself.
    Nothing of the picture is cut off."""
    w, h = img.size
    W, H = (round(h * ratio), h) if w / h < ratio else (w, round(w / ratio))
    scale = max(W / w, H / h)
    bg = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
    left, top = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((left, top, left + W, top + H)).filter(ImageFilter.GaussianBlur(max(W, H) / 40))
    bg = ImageEnhance.Brightness(bg).enhance(0.72)
    bg.paste(img, ((W - w) // 2, (H - h) // 2))
    return bg


def _jpeg(img: Image.Image, max_bytes: int = 1_000_000) -> bytes:
    """JPEG under 1 MB (Bluesky's limit; far below every other network's), as sharp as that allows."""
    for q in (90, 86, 82, 78, 74, 70):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= max_bytes:
            break
    return buf.getvalue()


def _open(data: bytes, number: int) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise UploadError(f"Picture {number} can't be opened. Use a JPG, PNG or WebP picture.") from None
    return _rgb(img)


def prepare_picture(data: bytes, ratio: float | None = None, number: int = 1) -> tuple[bytes, dict]:
    """A client picture → a JPEG every network accepts. `ratio` = the shape to match (a carousel's first picture);
    without it, a picture outside Instagram's 4:5 to 1.91:1 gets the nearest allowed shape."""
    img = _open(data, number)
    w, h = img.size
    if min(w, h) < MIN_SIDE:
        raise UploadError(f"Picture {number} is too small ({w}×{h} px). Use one at least {MIN_SIDE} px on each side.")
    img.thumbnail((2160, 2160), Image.LANCZOS)  # big phone pictures: smaller first, the rest is much faster
    shape = w / h
    want = ratio or min(max(shape, TALLEST), WIDEST)
    fitted = abs(shape - want) / want > 0.01
    if fitted:
        img = fit_shape(img, want)
    img.thumbnail((1440, 1800), Image.LANCZOS)  # Instagram shows at most 1440 px wide; Buffer resizes bigger ones
    return _jpeg(img), {"w": img.width, "h": img.height, "ratio": round(want, 4), "fitted": fitted,
                        "original": [w, h]}


def prepare_cover(data: bytes, name: str = "The cover picture") -> bytes:
    """The video's cover frame (made by the browser): used on the dashboard and in the approval email."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise UploadError(f"{name} of the video can't be read. Pick the video again.") from None
    img = _rgb(img)
    img.thumbnail((1080, 1920), Image.LANCZOS)
    return _jpeg(img)


def _small(img: Image.Image, size: int = 640) -> bytes:
    """640 px is plenty for the AI to see what is in a picture, and keeps it within Groq's per-minute token
    allowance (a picture costs about 1 token per 400 pixels)."""
    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def _sheet(pictures: list[Image.Image], first_number: int) -> bytes:
    """Up to 4 pictures on one 2×2 contact sheet, numbered, so the AI sees a whole carousel in one request."""
    cell, gap = 300, 8
    sheet = Image.new("RGB", (cell * 2 + gap, cell * 2 + gap), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    for i, pic in enumerate(pictures):
        p = pic.copy()
        p.thumbnail((cell, cell), Image.LANCZOS)
        x, y = (i % 2) * (cell + gap), (i // 2) * (cell + gap)
        sheet.paste(p, (x + (cell - p.width) // 2, y + (cell - p.height) // 2))
        draw.rounded_rectangle((x + 6, y + 6, x + 44, y + 42), 8, fill=(11, 18, 48))
        draw.text((x + 25, y + 24), str(first_number + i), fill="white", font=font, anchor="mm")
    buf = io.BytesIO()
    sheet.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def pictures_for_ai(jpegs: list[bytes]) -> list[bytes]:
    """At most 3 pictures for the picture-reading AI (its limit): one each, or numbered contact sheets."""
    imgs = [Image.open(io.BytesIO(b)).convert("RGB") for b in jpegs]
    if len(imgs) <= 3:
        return [_small(i) for i in imgs]
    groups = [imgs[i:i + 4] for i in range(0, min(len(imgs), 12), 4)]
    return [_sheet(g, n * 4 + 1) for n, g in enumerate(groups)]


async def save_pictures(kind: str, files: list) -> dict:
    """Check and get ready all pictures first (so a bad one stops everything), then store them."""
    if kind == "image" and len(files) != 1:
        raise UploadError("Pick one picture.")
    if kind == "carousel" and not 2 <= len(files) <= MAX_IMAGES:
        raise UploadError(f"A carousel needs 2 to {MAX_IMAGES} pictures (you picked {len(files)}).")
    ready, ratio = [], None
    for n, f in enumerate(files, 1):
        data = await f.read(MAX_IMAGE_MB * MB + 1)
        if not data:
            raise UploadError(f"Picture {n} is empty. Pick it again.")
        if len(data) > MAX_IMAGE_MB * MB:
            raise UploadError(f"Picture {n} is bigger than {MAX_IMAGE_MB} MB. Use a smaller copy.")
        jpeg, info = await asyncio.to_thread(prepare_picture, data, ratio, n)
        ratio = ratio or info["ratio"]  # the carousel's later pictures get the first one's shape
        ready.append((jpeg, info))
    store, batch, items = get_store(), uuid.uuid4().hex, []
    for n, (jpeg, info) in enumerate(ready, 1):
        path, thumb_path = f"uploads/{batch}/{n}.jpg", f"uploads/{batch}/{n}-thumb.jpg"
        url = await store.save_file(path, jpeg, "image/jpeg")
        thumb = await store.save_file(thumb_path, await asyncio.to_thread(thumbnail, jpeg), "image/jpeg")
        items.append({"path": path, "url": url, "thumb": thumb, "thumb_path": thumb_path, **info})
    return {"kind": kind, "batch": batch, "items": items}


# ---------- videos ----------

def new_video_path(name: str, size: int) -> tuple[str, str]:
    """Where a new video goes in storage, after checking its type and size. Returns (path, content type)."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in VIDEO_TYPES:
        raise UploadError("Use an MP4 or MOV video (the formats every network accepts).")
    if size <= 0:
        raise UploadError("This video file is empty.")
    if size > max_video_bytes():
        raise UploadError(f"This video is {size / MB:.0f} MB; the limit is {get_settings().max_video_mb} MB. "
                          "Trim it, or export it at 1080p.")
    return f"uploads/{uuid.uuid4().hex}/video.{ext}", VIDEO_TYPES[ext]


async def save_video(form) -> dict:
    """The browser has sent the video straight to storage; check what arrived and keep its cover and frames."""
    store = get_store()
    path = str(form.get("video_path") or "")
    m = VIDEO_PATH.match(path)
    if not m:
        raise UploadError("The video is missing. Pick the video again.")
    try:
        meta = json.loads(str(form.get("video_meta") or "{}"))
        duration, width, height = float(meta.get("duration") or 0), int(meta.get("width") or 0), int(meta.get("height") or 0)
    except (ValueError, TypeError):
        raise UploadError("The video details are missing. Pick the video again.") from None
    info = await store.file_info(path)
    if not info:
        raise UploadError("The video didn't finish uploading. Please try again.")
    size = int(info.get("size") or 0)
    problem = None
    if size > max_video_bytes():
        problem = f"This video is {size / MB:.0f} MB; the limit is {get_settings().max_video_mb} MB."
    elif (await store.read_head(path, 16))[4:8] not in CONTAINER_BOXES:
        problem = "This file is not a real MP4 or MOV video."
    elif not MIN_SECONDS <= duration <= MAX_SECONDS:
        problem = f"The video must be {secs(MIN_SECONDS)} to {secs(MAX_SECONDS)} long (this one is {secs(duration)})."
    elif min(width, height) < MIN_VIDEO_SIDE:
        problem = f"The video is too small ({width}×{height}). Use one at least {MIN_VIDEO_SIDE} px on each side."
    if problem:
        try:
            await store.delete_file(path)
        except Exception:
            pass
        raise UploadError(problem)
    cover = form.get("poster")
    if not hasattr(cover, "read"):
        raise UploadError("The cover picture of the video is missing. Pick the video again.")
    cover_jpeg = await asyncio.to_thread(prepare_cover, await cover.read(10 * MB))
    frames = [f for f in form.getlist("frames") if hasattr(f, "read")][:3]
    frame_jpegs = [await asyncio.to_thread(prepare_cover, await f.read(10 * MB), "A frame") for f in frames]

    batch = m.group(1)
    poster = {"path": f"uploads/{batch}/cover.jpg", "thumb_path": f"uploads/{batch}/cover-thumb.jpg"}
    poster["url"] = await store.save_file(poster["path"], cover_jpeg, "image/jpeg")
    poster["thumb"] = await store.save_file(poster["thumb_path"], await asyncio.to_thread(thumbnail, cover_jpeg),
                                            "image/jpeg")
    frame_paths = []
    for n, jpeg in enumerate(frame_jpegs or [cover_jpeg], 1):
        frame_paths.append(f"uploads/{batch}/frame{n}.jpg")
        await store.save_file(frame_paths[-1], jpeg, "image/jpeg")
    codec = str(meta.get("codec") or "unknown").lower()
    video = {
        "path": path, "url": store.public_url(path), "size": size, "duration": round(duration, 2), "width": width,
        "height": height, "codec": codec if codec in ("h264", "hevc", "av1", "unknown") else "unknown",
        "name": str(meta.get("name") or "")[:120],
        "cover_ms": max(0, min(int(float(meta.get("cover_ms") or 0)), int(duration * 1000))),
    }
    return {"kind": "video", "batch": batch, "video": video, "poster": poster, "frames": frame_paths}
