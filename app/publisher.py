"""Publishing through Buffer's API (https://developers.buffer.com).

The channels are read from Buffer every time a post goes out, so whatever the client connects, reconnects or
removes in Buffer is picked up by itself. Supported: Facebook, Instagram, LinkedIn, X, Threads, TikTok (picture
posts), Pinterest, Google Business Profile, Bluesky, Mastodon and Substack. YouTube (video only), WhatsApp and
Start Page channels are left alone.
"""

import asyncio
import logging
import re
import time

import httpx

from .config import get_settings
from .uploads import IMAGES_PER_POST, video_problem

log = logging.getLogger("buffer")
API = "https://api.buffer.com"
SERVICES = {"facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn", "twitter": "X (Twitter)",
            "threads": "Threads", "tiktok": "TikTok", "pinterest": "Pinterest", "googlebusiness": "Google Business",
            "bluesky": "Bluesky", "mastodon": "Mastodon", "substack": "Substack"}
# Buffer channel type -> caption key (the AI writes one caption per key)
CAPTION_FOR = {"facebook": "facebook", "instagram": "instagram", "linkedin": "linkedin", "twitter": "x",
               "threads": "threads", "tiktok": "tiktok", "pinterest": "pinterest", "googlebusiness": "googlebusiness",
               "bluesky": "bluesky", "mastodon": "mastodon", "substack": "substack"}
# safe text length per caption key, used when a channel has no caption of its own (it was connected later)
FALLBACK_LIMIT = {"x": 260, "threads": 480, "bluesky": 290, "mastodon": 480, "pinterest": 480,
                  "googlebusiness": 1400, "tiktok": 2100, "instagram": 2100, "substack": 1900, "linkedin": 2900}

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id dueAt } }
    ... on MutationError { message }
  }
}"""


class BufferError(RuntimeError):
    pass


async def gql(query: str, variables: dict | None = None) -> dict:
    key = get_settings().buffer_api_key
    if not key:
        raise BufferError("Set BUFFER_API_KEY in .env")
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt in range(3):
            r = await client.post(API, json={"query": query, "variables": variables or {}},
                                  headers={"Authorization": f"Bearer {key}"})
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(min(float(r.headers.get("Retry-After", "10")), 30))
                continue
            break
    if r.status_code >= 400:
        raise BufferError(f"Buffer HTTP {r.status_code}: {r.text[:300]}")
    body = r.json()
    if body.get("errors"):
        raise BufferError("Buffer: " + "; ".join(e.get("message", "?") for e in body["errors"]))
    return body["data"]


async def list_channels() -> list[dict]:
    orgs = await gql("query { account { organizations { id name } } }")
    channels = []
    for org in orgs["account"]["organizations"]:
        data = await gql(
            "query C($id: OrganizationId!) { channels(input: {organizationId: $id}) "
            "{ id name service isQueuePaused isDisconnected isLocked "
            "metadata { ... on PinterestMetadata { boards { serviceId name } } } } }",
            {"id": org["id"]},
        )
        channels += [{**c, "organization": org["name"]} for c in data["channels"]]
    return channels


async def target_channels() -> list[dict]:
    wanted = get_settings().buffer_channel_id_list
    channels = await list_channels()
    if wanted:
        return [c for c in channels if c["id"] in wanted]
    return [c for c in channels if c["service"] in SERVICES]


async def caption_platforms() -> list[str]:
    """The caption keys for the channels posts go to right now (Facebook always included: it is the base text)."""
    keys = [CAPTION_FOR[c["service"]] for c in await target_channels() if c["service"] in CAPTION_FOR]
    return list(dict.fromkeys(["facebook", *keys]))


def _shorten(text: str, limit: int) -> str:
    """Cut a long caption at a line or sentence end before the limit (never mid-word)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    cut = max(head.rfind("\n"), head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut < limit * 0.5:
        cut = head.rfind(" ")
    return head[:cut + 1].rstrip() + ("" if head[cut] in ".!?" else "…")


def caption_for(service: str, captions: dict) -> str:
    """This channel's own caption; if it has none (connected after the post was written), a fitted Facebook text."""
    key = CAPTION_FOR.get(service, "facebook")
    if captions.get(key):
        return captions[key]
    text = captions.get("facebook") or ""
    if key == "googlebusiness":  # Google rejects posts with phone numbers; hashtags do nothing there
        text = re.sub(r"#\w+", "", re.sub(r"[📞☎]?\s*\+?\d[\d\s-]{7,}\d", "", text))
        text = re.sub(r"[ \t]+", " ", re.sub(r"[ ·|]+\n", "\n", text)).strip()
    return _shorten(text, FALLBACK_LIMIT.get(key, 2000))


_label_cache: dict = {"at": 0.0, "text": None}


async def target_label() -> str:
    """Where an approved post goes, in words, e.g. 'Facebook (Amanasoft It Solutions)'. Cached for 10 minutes."""
    if _label_cache["text"] and time.monotonic() - _label_cache["at"] < 600:
        return _label_cache["text"]
    try:
        chans = await target_channels()
    except Exception:
        return "your social channels"
    text = ", ".join(f"{SERVICES.get(c['service'], c['service'])} ({c['name']})" for c in chans) or "no channels yet"
    _label_cache.update(at=time.monotonic(), text=text)
    return text


def _website() -> str:
    from .brand import load_brand
    return load_brand().company.get("website_url") or f"https://{load_brand().company['website']}"


def media_assets(service: str, media: dict | None, image_url: str) -> list[dict]:
    """Buffer's assets for this channel: the designed picture, the client's own pictures (as many as that network
    takes in one post: a carousel on Instagram), or the client's video."""
    if not media:
        return [{"image": {"url": image_url}}]
    if media.get("kind") == "video":
        video = {"url": media["video"]["url"]}
        if service in ("instagram", "tiktok", "pinterest") and media["video"].get("cover_ms"):
            video["metadata"] = {"thumbnailOffset": int(media["video"]["cover_ms"])}  # the cover frame the client chose
        return [{"video": video}]
    return [{"image": {"url": item["url"]}} for item in media["items"][:IMAGES_PER_POST.get(service, 10)]]


def skip_reason(service: str, media: dict | None) -> str | None:
    """Why this post can't go to this kind of channel at all (it is skipped there, not failed)."""
    if media and media.get("kind") == "video":
        return video_problem(service, media["video"])
    return None


def _input(channel: dict, captions: dict, image_url: str, mode: str | None = None, media: dict | None = None) -> dict:
    service = channel["service"]
    s = get_settings()
    video = bool(media and media.get("kind") == "video")
    text = caption_for(service, captions)
    if video and service == "tiktok":
        text = _shorten(text, 150)  # Buffer: a TikTok video's text can be at most 150 characters
    data = {
        "channelId": channel["id"],
        "text": text,
        "schedulingType": "automatic",
        "mode": mode or s.publish_mode,
        "assets": media_assets(service, media, image_url),
        "aiAssisted": True,
    }
    if service == "facebook":
        data["metadata"] = {"facebook": {"type": "post"}}  # Buffer posts a vertical video as a Reel by itself
        if video and captions.get("title"):
            data["assets"][0]["video"]["metadata"] = {"title": captions["title"][:100]}  # shown above the video
    elif service == "instagram":
        # both fields are required by Buffer; Instagram publishes every video as a Reel, several pictures as a carousel
        data["metadata"] = {"instagram": {"type": "reel" if video else "post", "shouldShareToFeed": True}}
    elif service == "tiktok":  # TikTok asks for AI-made pictures to be labelled; the client's own media are not
        data["metadata"] = {"tiktok": {"title": (captions.get("tiktok_title") or captions.get("title") or "")[:90],
                                       "isAiGenerated": not media}}
    elif service == "pinterest":
        boards = (channel.get("metadata") or {}).get("boards") or []
        pin = {"title": (captions.get("pinterest_title") or captions.get("title") or "")[:100], "url": _website()}
        if boards:
            pin["boardServiceId"] = boards[0]["serviceId"]
        data["metadata"] = {"pinterest": pin}
    elif service == "googlebusiness":  # a "What's new" post with a Learn more button to the website
        data["metadata"] = {"google": {"type": "whats_new", "detailsWhatsNew": {"button": "learn_more", "link": _website()}}}
    elif service == "threads" and captions.get("threads_topic"):
        data["metadata"] = {"threads": {"topic": captions["threads_topic"]}}
    return data


async def publish(captions: dict, image_url: str, only_channel_ids: list[str] | None = None,
                  mode: str | None = None, media: dict | None = None) -> dict:
    """Send the post to each channel. Returns {"dry_run": bool, "channels": [...per channel result...]}.
    media = the client's own pictures or video (see uploads.py); without it the designed picture is posted."""
    s = get_settings()
    channels = await target_channels()
    if only_channel_ids:
        channels = [c for c in channels if c["id"] in only_channel_ids]
    if not channels:
        raise BufferError("No Facebook, Instagram, LinkedIn, X or Threads channel found in Buffer. Connect them in Buffer first.")
    results = []
    for ch in channels:
        res = {"channel_id": ch["id"], "service": ch["service"], "name": ch["name"],
               "label": SERVICES.get(ch["service"], ch["service"])}
        why = skip_reason(ch["service"], media)
        if why:
            results.append({**res, "ok": False, "skipped": True, "error": f"Skipped: {why}."})
            continue
        if ch.get("isDisconnected") or ch.get("isLocked"):
            results.append({**res, "ok": False, "error": "Channel is disconnected or locked in Buffer; reconnect it there."})
            continue
        if s.dry_run_publish:
            results.append({**res, "ok": True, "buffer_post_id": "dry-run"})
            continue
        try:
            data = await gql(CREATE_POST, {"input": _input(ch, captions, image_url, mode, media)})
            out = data["createPost"]
            if out.get("post"):
                results.append({**res, "ok": True, "buffer_post_id": out["post"]["id"], "due_at": out["post"].get("dueAt")})
            else:
                results.append({**res, "ok": False, "error": out.get("message", "Unknown Buffer error")})
        except (BufferError, httpx.HTTPError) as e:
            results.append({**res, "ok": False, "error": str(e)})
    return {"dry_run": s.dry_run_publish, "channels": results}
