"""Gemini image generation through browser cookies, with two accounts (A then B).

When an account's cookie stops working it is marked dead, the other account is used, and an
alert email explains how to put a fresh cookie in .env (or Render's Environment tab).
"""

import asyncio
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings

os.environ.setdefault("GEMINI_COOKIE_PATH", str(get_settings().data_dir / "gemini_cookies"))

from gemini_webapi import GeminiClient  # noqa: E402
from gemini_webapi.exceptions import (  # noqa: E402
    AuthError, TemporarilyBlockedError, UsageLimitExceededError,
)
from gemini_webapi.types.image import GeneratedImage  # noqa: E402

from . import emailer  # noqa: E402
from .store import get_store  # noqa: E402

log = logging.getLogger("gemini")
SLOTS = ("A", "B")


class ImageGenError(RuntimeError):
    pass


class NoImageReturned(ImageGenError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GeminiPool:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.clients: dict[str, GeminiClient] = {}

    # ----- cookies and status (kept in the store so they survive restarts) -----

    async def cookies(self, slot: str) -> tuple[str, str]:
        """The cookie to use for an account.

        The app saves Gemini's refreshed copy of the cookie (so restarts keep working). A cookie you put in
        .env (or Render's Environment tab) wins whenever it is new: a different login, or the same login with a
        fresh __Secure-1PSIDTS that you pasted. Otherwise the newest refreshed copy is used."""
        s = get_settings()
        store = get_store()
        env_psid = (getattr(s, f"gemini_{slot.lower()}_1psid") or "").strip()
        env_ts = (getattr(s, f"gemini_{slot.lower()}_1psidts") or "").strip()
        saved = await store.get_setting(f"gemini_cookie_{slot}") or {}
        if env_psid:
            same_login = saved.get("psid") == env_psid
            if same_login and "env_psidts" not in saved:
                saved = {**saved, "env_psidts": env_ts}  # older saved copy: remember which .env value it came from
                await store.set_setting(f"gemini_cookie_{slot}", saved)
            elif not same_login or saved.get("env_psidts") != env_ts:
                saved = {"psid": env_psid, "psidts": env_ts, "env_psidts": env_ts, "saved_at": _now()}
                await store.set_setting(f"gemini_cookie_{slot}", saved)
                await store.set_setting(f"gemini_status_{slot}", {"state": "unknown", "checked_at": _now()})
                log.info("New cookie for Gemini account %s taken from the environment", slot)
        if saved.get("psid"):
            return saved["psid"], saved.get("psidts", "")
        return env_psid, env_ts

    async def status(self, slot: str) -> dict:
        psid, _ = await self.cookies(slot)
        st = await get_store().get_setting(f"gemini_status_{slot}") or {}
        if not psid:
            st = {**st, "state": "missing"}
        return {"slot": slot, "state": st.get("state", "unknown"), "error": st.get("error"),
                "checked_at": st.get("checked_at"), "last_ok": st.get("last_ok")}

    async def _set_status(self, slot: str, state: str, error: str | None = None):
        store = get_store()
        prev = await store.get_setting(f"gemini_status_{slot}") or {}
        st = {**prev, "state": state, "error": error, "checked_at": _now()}
        if state == "ok":
            st["last_ok"] = _now()
        await store.set_setting(f"gemini_status_{slot}", st)
        if state == "dead" and prev.get("state") != "dead":
            await emailer.alert_cookie_dead(slot, error or "")

    async def _persist_rotated(self, slot: str, client: GeminiClient):
        """Gemini refreshes __Secure-1PSIDTS over time; save the newest value so a restart keeps working."""
        try:
            jar = {c.name: c.value for c in client.cookies.jar}
        except Exception:
            return
        store = get_store()
        saved = await store.get_setting(f"gemini_cookie_{slot}") or {}
        psid, psidts = await self.cookies(slot)
        new_ts = jar.get("__Secure-1PSIDTS")
        if new_ts and jar.get("__Secure-1PSID") == psid and new_ts != psidts:
            await store.set_setting(f"gemini_cookie_{slot}", {**saved, "psid": psid, "psidts": new_ts, "saved_at": _now()})

    # ----- clients -----

    async def _drop_client(self, slot: str):
        client = self.clients.pop(slot, None)
        if client:
            try:
                await client.close()
            except Exception:
                pass

    async def _client(self, slot: str) -> GeminiClient:
        if slot in self.clients:
            return self.clients[slot]
        psid, psidts = await self.cookies(slot)
        if not psid:
            raise AuthError(f"No cookie saved for account {slot}")
        client = GeminiClient(psid, psidts or None, proxy=get_settings().gemini_proxy or None)
        await client.init(timeout=240, auto_close=False, auto_refresh=True, refresh_interval=600)
        if not client._check_account_status():
            status = getattr(client.account_status, "name", str(client.account_status))
            await client.close()
            raise AuthError(f"Google account not usable for Gemini ({status})")
        self.clients[slot] = client
        await self._persist_rotated(slot, client)
        return client

    async def check(self, slot: str) -> dict:
        """Fresh login test for one account (no image is generated)."""
        async with self.lock:
            await self._drop_client(slot)
            try:
                await self._client(slot)
                await self._set_status(slot, "ok")
            except AuthError as e:
                await self._set_status(slot, "missing" if "No cookie" in str(e) else "dead", str(e))
            except Exception as e:
                await self._set_status(slot, "error", f"{type(e).__name__}: {e}")
        return await self.status(slot)

    # ----- generation -----

    async def _generate_with(self, slot: str, prompt: str) -> bytes:
        client = await self._client(slot)
        chat = client.start_chat(model=get_settings().gemini_model or None)
        out = await chat.send_message(prompt)
        images = [i for i in out.images if isinstance(i, GeneratedImage)]
        if not images:
            # Gemini sometimes answers with a question first; nudge it once in the same chat.
            out = await chat.send_message("Please generate the image now exactly as described, with no text in it.")
            images = [i for i in out.images if isinstance(i, GeneratedImage)]
        if not images:
            raise NoImageReturned(f"Gemini replied without an image: {(out.text or '')[:300]}")
        tmp = Path(tempfile.gettempdir()) / "post-agent"
        path = await images[0].save(path=str(tmp), filename=f"{uuid.uuid4().hex}.png", full_size=True)
        data = Path(path).read_bytes()
        Path(path).unlink(missing_ok=True)
        await self._persist_rotated(slot, client)
        return data

    async def generate(self, prompt: str, only_slot: str | None = None) -> tuple[bytes, str]:
        """Returns (image bytes, slot used). Tries account A, then B (or only `only_slot`)."""
        errors = []
        async with self.lock:
            for slot in ([only_slot] if only_slot else SLOTS):
                st = await self.status(slot)
                if st["state"] == "missing":
                    continue
                try:
                    data = await self._generate_with(slot, prompt)
                    await self._set_status(slot, "ok")
                    return data, slot
                except NoImageReturned:
                    raise
                except AuthError as e:
                    await self._drop_client(slot)
                    await self._set_status(slot, "dead", str(e))
                    errors.append(f"{slot}: cookie expired ({e})")
                except (UsageLimitExceededError, TemporarilyBlockedError) as e:
                    await self._set_status(slot, "limited", str(e))
                    errors.append(f"{slot}: usage limit ({e})")
                except Exception as e:
                    await self._drop_client(slot)
                    await self._set_status(slot, "error", f"{type(e).__name__}: {e}")
                    errors.append(f"{slot}: {type(e).__name__}: {e}")
                    log.exception("Gemini account %s failed", slot)
        if not errors:
            raise ImageGenError("No Gemini cookies set. Put them in GEMINI_A_1PSID / GEMINI_A_1PSIDTS (.env or Render Environment).")
        raise ImageGenError("All Gemini accounts failed. " + " | ".join(errors))

    async def health_loop(self):
        """Test both cookies shortly after start and then every few hours. In between, save the
        cookie values Gemini refreshes in the background, so a Render restart doesn't lose them."""
        every = max(get_settings().gemini_health_check_hours, 0.5) * 3600
        next_check = asyncio.get_running_loop().time() + 30
        while True:
            await asyncio.sleep(30 if not self.clients else 600)
            for slot, client in list(self.clients.items()):
                try:
                    await self._persist_rotated(slot, client)
                except Exception:
                    log.exception("Could not save refreshed cookie for %s", slot)
            if asyncio.get_running_loop().time() < next_check:
                continue
            next_check = asyncio.get_running_loop().time() + every
            for slot in SLOTS:
                try:
                    if (await self.status(slot))["state"] != "missing":
                        await self.check(slot)
                except Exception:
                    log.exception("Cookie health check failed for %s", slot)


pool = GeminiPool()
