"""Add small dashboard thumbnails to posts made before thumbnails existed. Safe to run more than once.

Run:  .venv/bin/python scripts/make_thumbnails.py
"""
import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.composer import thumbnail  # noqa: E402
from app.store import get_store  # noqa: E402


async def main():
    store = get_store()
    made = 0
    async with httpx.AsyncClient(timeout=120) as client:
        for post in await store.list_posts(500):
            changes = {}
            brief = post.get("brief") or {}
            if post.get("final_image_url") and not brief.get("_thumb"):
                data = (await client.get(post["final_image_url"])).content
                url = await store.save_file(f"{post['id']}/v{post['attempt']}-thumb.jpg", thumbnail(data), "image/jpeg")
                changes["brief"] = {**brief, "_thumb": url}
                made += 1
            history = post.get("history") or []
            new_history, touched = [], False
            for h in history:
                hb = h.get("brief") or {}
                if h.get("final_image_url") and not hb.get("_thumb"):
                    data = (await client.get(h["final_image_url"])).content
                    url = await store.save_file(f"{post['id']}/v{h.get('attempt', 0)}-thumb.jpg", thumbnail(data), "image/jpeg")
                    h = {**h, "brief": {**hb, "_thumb": url}}
                    touched, made = True, made + 1
                new_history.append(h)
            if touched:
                changes["history"] = new_history
            if changes:
                await store.update_post(post["id"], **changes)
                print(f"{post['id'][:8]}: thumbnails added")
    print(f"Done: {made} thumbnails made.")


asyncio.run(main())
