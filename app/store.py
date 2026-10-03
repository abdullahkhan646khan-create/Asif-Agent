"""Where posts, settings and images are kept.

Supabase is used when SUPABASE_URL and SUPABASE_SERVICE_KEY are set (required on Render,
because Render's disk is wiped on every restart and Buffer needs public image links).
Without them, a local SQLite file and a local folder are used, which is handy for a first
test on your own machine.
"""

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import get_settings

JSON_FIELDS = ("brief", "captions", "history", "publish_results", "prefs")
POST_COLUMNS = (
    "id", "created_at", "updated_at", "idea", "template_pref", "status", "stage_note", "attempt",
    "brief", "captions", "raw_image_path", "final_image_path", "final_image_url", "reject_reason",
    "history", "publish_results", "error", "reviewed_at", "published_at", "prefs", "scheduled_for",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class StoreError(RuntimeError):
    pass


class LocalStore:
    kind = "local"

    def __init__(self, data_dir: Path, base_url: str):
        self.db_path = data_dir / "app.db"
        self.media_dir = data_dir / "media"
        self.base_url = base_url
        self.media_dir.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """create table if not exists posts (
                    id text primary key, created_at text, updated_at text, idea text,
                    template_pref text, status text, stage_note text, attempt integer default 1,
                    brief text, captions text, raw_image_path text, final_image_path text,
                    final_image_url text, reject_reason text, history text default '[]',
                    publish_results text, error text, reviewed_at text, published_at text, prefs text,
                    scheduled_for text)"""
            )
            cols = {r["name"] for r in c.execute("pragma table_info(posts)")}
            for col in ("prefs", "scheduled_for"):  # databases made before these columns existed
                if col not in cols:
                    c.execute(f"alter table posts add column {col} text")
            c.execute("create table if not exists settings (key text primary key, value text, updated_at text)")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        post = dict(row)
        for f in JSON_FIELDS:
            post[f] = json.loads(post[f]) if post.get(f) else ([] if f == "history" else None)
        return post

    def _run(self, fn):
        return asyncio.to_thread(fn)

    async def create_post(self, idea: str, template_pref: str | None, prefs: dict | None = None,
                          scheduled_for: str | None = None) -> dict:
        post = {
            "id": str(uuid.uuid4()), "created_at": now_iso(), "updated_at": now_iso(), "idea": idea,
            "template_pref": template_pref, "status": "queued", "stage_note": "Waiting to start",
            "attempt": 1, "history": "[]", "prefs": json.dumps(prefs or {}), "scheduled_for": scheduled_for,
        }

        def fn():
            with self._conn() as c:
                cols = ",".join(post)
                c.execute(f"insert into posts ({cols}) values ({','.join('?' * len(post))})", list(post.values()))
        await self._run(fn)
        return await self.get_post(post["id"])

    async def get_post(self, post_id: str) -> dict | None:
        def fn():
            with self._conn() as c:
                return self._decode(c.execute("select * from posts where id=?", (post_id,)).fetchone())
        return await self._run(fn)

    async def update_post(self, post_id: str, **fields: Any) -> dict:
        fields["updated_at"] = now_iso()
        for f in JSON_FIELDS:
            if f in fields and fields[f] is not None:
                fields[f] = json.dumps(fields[f], ensure_ascii=False)

        def fn():
            with self._conn() as c:
                sets = ",".join(f"{k}=?" for k in fields)
                c.execute(f"update posts set {sets} where id=?", [*fields.values(), post_id])
        await self._run(fn)
        return await self.get_post(post_id)

    async def claim(self, post_id: str, from_statuses: list[str], to_status: str, **fields: Any) -> dict | None:
        """Move a post to a new status only if it is currently in one of from_statuses (no double actions)."""
        fields.update(status=to_status, updated_at=now_iso())
        for f in JSON_FIELDS:
            if f in fields and fields[f] is not None:
                fields[f] = json.dumps(fields[f], ensure_ascii=False)

        def fn():
            with self._conn() as c:
                sets = ",".join(f"{k}=?" for k in fields)
                q = f"update posts set {sets} where id=? and status in ({','.join('?' * len(from_statuses))})"
                return c.execute(q, [*fields.values(), post_id, *from_statuses]).rowcount
        return await self.get_post(post_id) if await self._run(fn) else None

    async def list_posts(self, limit: int = 50) -> list[dict]:
        def fn():
            with self._conn() as c:
                rows = c.execute("select * from posts order by created_at desc limit ?", (limit,)).fetchall()
                return [self._decode(r) for r in rows]
        return await self._run(fn)

    async def posts_in_status(self, statuses: list[str]) -> list[dict]:
        def fn():
            with self._conn() as c:
                q = f"select * from posts where status in ({','.join('?' * len(statuses))})"
                return [self._decode(r) for r in c.execute(q, statuses).fetchall()]
        return await self._run(fn)

    async def get_setting(self, key: str) -> Any:
        def fn():
            with self._conn() as c:
                row = c.execute("select value from settings where key=?", (key,)).fetchone()
                return json.loads(row["value"]) if row else None
        return await self._run(fn)

    async def set_setting(self, key: str, value: Any) -> None:
        def fn():
            with self._conn() as c:
                c.execute(
                    "insert into settings (key, value, updated_at) values (?,?,?) "
                    "on conflict(key) do update set value=excluded.value, updated_at=excluded.updated_at",
                    (key, json.dumps(value), now_iso()),
                )
        await self._run(fn)

    async def save_file(self, path: str, data: bytes, content_type: str) -> str:
        dest = self.media_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(dest.write_bytes, data)
        return f"{self.base_url}/media/{path}"

    async def read_file(self, path: str) -> bytes:
        return await asyncio.to_thread((self.media_dir / path).read_bytes)

    async def ping(self) -> bool:
        await self.get_setting("ping")
        return True


class SupabaseStore:
    kind = "supabase"

    def __init__(self, url: str, key: str, bucket: str):
        self.url = url.rstrip("/")
        self.bucket = bucket
        headers = {"apikey": key}
        # Legacy keys are JWTs and also go in Authorization; new sb_secret_ keys go in apikey only.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        self.client = httpx.AsyncClient(base_url=self.url, headers=headers, timeout=60)

    async def _rest(self, method: str, path: str, **kw) -> Any:
        r = await self.client.request(method, f"/rest/v1/{path}", **kw)
        if r.status_code >= 400:
            raise StoreError(f"Supabase {method} {path} failed ({r.status_code}): {r.text[:300]}")
        return r.json() if r.content else None

    async def create_post(self, idea: str, template_pref: str | None, prefs: dict | None = None,
                          scheduled_for: str | None = None) -> dict:
        body = {"idea": idea, "template_pref": template_pref, "status": "queued", "stage_note": "Waiting to start",
                "prefs": prefs or {}, "scheduled_for": scheduled_for}
        rows = await self._rest("POST", "posts", json=body, headers={"Prefer": "return=representation"})
        return rows[0]

    async def get_post(self, post_id: str) -> dict | None:
        rows = await self._rest("GET", "posts", params={"id": f"eq.{post_id}", "select": "*"})
        return rows[0] if rows else None

    async def update_post(self, post_id: str, **fields: Any) -> dict:
        fields["updated_at"] = now_iso()
        rows = await self._rest(
            "PATCH", "posts", params={"id": f"eq.{post_id}"}, json=fields, headers={"Prefer": "return=representation"}
        )
        return rows[0]

    async def claim(self, post_id: str, from_statuses: list[str], to_status: str, **fields: Any) -> dict | None:
        fields.update(status=to_status, updated_at=now_iso())
        rows = await self._rest(
            "PATCH", "posts", params={"id": f"eq.{post_id}", "status": f"in.({','.join(from_statuses)})"},
            json=fields, headers={"Prefer": "return=representation"},
        )
        return rows[0] if rows else None

    async def list_posts(self, limit: int = 50) -> list[dict]:
        return await self._rest("GET", "posts", params={"select": "*", "order": "created_at.desc", "limit": str(limit)})

    async def posts_in_status(self, statuses: list[str]) -> list[dict]:
        return await self._rest("GET", "posts", params={"select": "*", "status": f"in.({','.join(statuses)})"})

    async def get_setting(self, key: str) -> Any:
        rows = await self._rest("GET", "settings", params={"key": f"eq.{key}", "select": "value"})
        return rows[0]["value"] if rows else None

    async def set_setting(self, key: str, value: Any) -> None:
        await self._rest(
            "POST", "settings", json={"key": key, "value": value, "updated_at": now_iso()},
            headers={"Prefer": "resolution=merge-duplicates"},
        )

    async def save_file(self, path: str, data: bytes, content_type: str) -> str:
        r = await self.client.post(
            f"/storage/v1/object/{self.bucket}/{path}", content=data,
            headers={"Content-Type": content_type, "x-upsert": "true", "Cache-Control": "max-age=31536000"},
        )
        if r.status_code >= 400:
            raise StoreError(f"Supabase upload failed ({r.status_code}): {r.text[:300]}")
        return f"{self.url}/storage/v1/object/public/{self.bucket}/{path}"

    async def read_file(self, path: str) -> bytes:
        r = await self.client.get(f"/storage/v1/object/{self.bucket}/{path}")
        if r.status_code >= 400:
            raise StoreError(f"Supabase download failed ({r.status_code}): {r.text[:300]}")
        return r.content

    async def ping(self) -> bool:
        await self._rest("GET", "settings", params={"select": "key", "limit": "1"})
        return True


_store: LocalStore | SupabaseStore | None = None


def get_store() -> LocalStore | SupabaseStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.use_supabase:
            _store = SupabaseStore(s.supabase_url, s.supabase_service_key, s.supabase_bucket)
        else:
            _store = LocalStore(s.data_dir, s.base_url)
    return _store
