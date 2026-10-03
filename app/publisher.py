"""Publishing through Buffer's API (https://developers.buffer.com): Facebook, X and Threads channels."""

import asyncio
import logging
import time

import httpx

from .config import get_settings

log = logging.getLogger("buffer")
API = "https://api.buffer.com"
SERVICES = {"facebook": "Facebook", "twitter": "X (Twitter)", "threads": "Threads"}
CAPTION_FOR = {"facebook": "facebook", "twitter": "x", "threads": "threads"}

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
            "{ id name service isQueuePaused isDisconnected isLocked } }",
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


_label_cache: dict = {"at": 0.0, "text": None}


async def target_label() -> str:
    """Where an approved post goes, in words, e.g. 'Facebook (Tesst)'. Cached for 10 minutes."""
    if _label_cache["text"] and time.monotonic() - _label_cache["at"] < 600:
        return _label_cache["text"]
    try:
        chans = await target_channels()
    except Exception:
        return "your social channels"
    text = ", ".join(f"{SERVICES.get(c['service'], c['service'])} ({c['name']})" for c in chans) or "no channels yet"
    _label_cache.update(at=time.monotonic(), text=text)
    return text


def _input(channel: dict, captions: dict, image_url: str, mode: str | None = None) -> dict:
    service = channel["service"]
    s = get_settings()
    data = {
        "channelId": channel["id"],
        "text": captions[CAPTION_FOR.get(service, "facebook")],
        "schedulingType": "automatic",
        "mode": mode or s.publish_mode,
        "assets": [{"image": {"url": image_url}}],
        "aiAssisted": True,
    }
    if service == "facebook":
        data["metadata"] = {"facebook": {"type": "post"}}
    elif service == "threads" and captions.get("threads_topic"):
        data["metadata"] = {"threads": {"topic": captions["threads_topic"]}}
    return data


async def publish(captions: dict, image_url: str, only_channel_ids: list[str] | None = None,
                  mode: str | None = None) -> dict:
    """Send the post to each channel. Returns {"dry_run": bool, "channels": [...per channel result...]}."""
    s = get_settings()
    channels = await target_channels()
    if only_channel_ids:
        channels = [c for c in channels if c["id"] in only_channel_ids]
    if not channels:
        raise BufferError("No Facebook, X or Threads channel found in Buffer. Connect them in Buffer first.")
    results = []
    for ch in channels:
        res = {"channel_id": ch["id"], "service": ch["service"], "name": ch["name"],
               "label": SERVICES.get(ch["service"], ch["service"])}
        if ch.get("isDisconnected") or ch.get("isLocked"):
            results.append({**res, "ok": False, "error": "Channel is disconnected or locked in Buffer; reconnect it there."})
            continue
        if s.dry_run_publish:
            results.append({**res, "ok": True, "buffer_post_id": "dry-run"})
            continue
        try:
            data = await gql(CREATE_POST, {"input": _input(ch, captions, image_url, mode)})
            out = data["createPost"]
            if out.get("post"):
                results.append({**res, "ok": True, "buffer_post_id": out["post"]["id"], "due_at": out["post"].get("dueAt")})
            else:
                results.append({**res, "ok": False, "error": out.get("message", "Unknown Buffer error")})
        except (BufferError, httpx.HTTPError) as e:
            results.append({**res, "ok": False, "error": str(e)})
    return {"dry_run": s.dry_run_publish, "channels": results}
