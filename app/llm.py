"""Text AI: Groq first, OpenRouter as automatic backup. Always returns parsed JSON.

Free Groq accounts allow about 8,000 tokens per minute per model, so a "rate limit" answer is
normal: we wait the few seconds Groq asks for and try again, and only then move to the next model.
"""

import asyncio
import json
import logging
import re
from typing import Callable

import httpx

from .config import get_settings

log = logging.getLogger("llm")

PROVIDERS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
MAX_WAIT = 30          # seconds we are willing to wait for a rate limit before trying the next model
RATE_LIMIT_RETRIES = 3
_missing_models: set[str] = set()  # models the provider said don't exist (skipped until restart)


class LLMError(RuntimeError):
    pass


class RateLimited(LLMError):
    def __init__(self, message: str, wait: float | None):
        super().__init__(message)
        self.wait = wait


def _candidates() -> list[tuple[str, str, str]]:
    s = get_settings()
    out = []
    if s.groq_api_key:
        out += [("groq", s.groq_api_key, m) for m in s.groq_model_list]
    if s.openrouter_api_key:
        out += [("openrouter", s.openrouter_api_key, m) for m in s.openrouter_model_list]
    return [c for c in out if f"{c[0]}/{c[2]}" not in _missing_models]


def _model_options(provider: str, model: str) -> dict:
    """Per-model settings: short 'thinking' for reasoning models, and a sensible answer length."""
    if provider == "groq" and model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low", "max_tokens": 3000}
    if provider == "groq" and model.startswith("qwen/"):
        return {"reasoning_format": "hidden", "max_tokens": 1500}
    return {"max_tokens": 3000}


def _wait_seconds(r: httpx.Response) -> float | None:
    """How long the provider asks us to wait (Retry-After header, or 'try again in 1m2.5s' text)."""
    header = r.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = re.search(r"try again in ((?:\d+h)?(?:\d+m(?!s))?(?:[\d.]+(?:ms|s))?)", r.text)
    if not m or not m.group(1):
        return None
    total = 0.0
    for value, unit in re.findall(r"([\d.]+)(h|ms|m|s)", m.group(1)):
        total += float(value) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in reply")
    return json.loads(text[start:end + 1])


async def _call(client: httpx.AsyncClient, provider: str, key: str, model: str, messages: list[dict],
                temperature: float, plain: bool = False) -> str:
    options = {"max_tokens": 3000} if plain else _model_options(provider, model)
    json_mode = not plain
    body = {"model": model, "messages": messages, "temperature": temperature, **options}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers.update({"HTTP-Referer": get_settings().base_url, "X-Title": "Amanasoft Post Agent"})
    r = await client.post(PROVIDERS[provider], json=body, headers=headers)
    if r.status_code == 400 and not plain and any(
            w in r.text for w in ("response_format", "reasoning_effort", "reasoning_format", "json_validate")):
        # this model doesn't accept one of the extras: one plain request (the JSON is still parsed from the text)
        return await _call(client, provider, key, model, messages, temperature, plain=True)
    if r.status_code == 429:
        raise RateLimited(f"{provider}/{model} rate limit: {r.text[:200]}", _wait_seconds(r))
    if r.status_code == 404 and "model" in r.text:
        _missing_models.add(f"{provider}/{model}")
    if r.status_code >= 400:
        raise LLMError(f"{provider}/{model} HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    usage = data.get("usage") or {}
    log.info("%s/%s used %s prompt + %s answer tokens", provider, model,
             usage.get("prompt_tokens"), usage.get("completion_tokens"))
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError) as e:
        raise LLMError(f"{provider}/{model} unexpected reply: {str(data)[:300]}") from e


async def _call_patiently(client, provider, key, model, messages, temperature, max_wait: float = MAX_WAIT) -> str:
    """One request, waiting out short rate limits (a few seconds) before giving up on this model."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return await _call(client, provider, key, model, messages, temperature)
        except RateLimited as e:
            wait = e.wait if e.wait is not None else 5 * (attempt + 1)
            if attempt == RATE_LIMIT_RETRIES or wait > max_wait:
                raise
            log.info("%s/%s is busy, waiting %.1fs (as asked) then trying again", provider, model, wait)
            await asyncio.sleep(wait + 0.5)
    raise LLMError("unreachable")


async def ask_json(system: str, user: str, check: Callable[[dict], list[str]] | None = None,
                   temperature: float = 0.7) -> tuple[dict, str]:
    """Ask for a JSON object. `check` returns a list of problems; the AI gets two chances to fix them.

    Returns (data, "provider/model"). Tries every configured model in order until one works.
    """
    candidates = _candidates()
    if not candidates:
        raise LLMError("No text AI configured. Set GROQ_API_KEY and/or OPENROUTER_API_KEY in .env")
    errors = []
    async with httpx.AsyncClient(timeout=120) as client:
        for provider, key, model in candidates:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            best = None
            for _ in range(3):
                try:
                    reply = await _call_patiently(client, provider, key, model, messages, temperature)
                except (LLMError, httpx.HTTPError) as e:
                    errors.append(str(e))
                    log.warning("LLM call failed: %s", e)
                    break
                try:
                    data = _extract_json(reply)
                except (ValueError, json.JSONDecodeError) as e:
                    errors.append(f"{provider}/{model}: bad JSON ({e})")
                    messages += [{"role": "assistant", "content": reply},
                                 {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."}]
                    continue
                problems = check(data) if check else []
                if not problems:
                    return data, f"{provider}/{model}"
                best = data
                messages += [{"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
                             {"role": "user", "content": "Fix these problems and reply with the full corrected JSON only:\n- "
                              + "\n- ".join(problems)}]
            if best is not None:
                # Still has small problems after two fixes; the caller cleans up what is left.
                return best, f"{provider}/{model}"
    raise LLMError("All text AI models failed: " + " | ".join(errors[-4:]))


MAX_PICTURES = 3  # Groq's picture models take at most 3 pictures in one request


async def ask_vision_json(system: str, text: str, pictures: list[bytes], temperature: float = 0.3) -> tuple[dict, str]:
    """Show up to 3 JPEG pictures to a model that can see, and get a JSON object back.

    Returns (data, "provider/model"). Tries every model in GROQ_VISION_MODELS in order."""
    import base64
    s = get_settings()
    candidates = [("groq", s.groq_api_key, m) for m in s.groq_vision_model_list
                  if s.groq_api_key and f"groq/{m}" not in _missing_models]
    if not candidates:
        raise LLMError("No picture-reading AI configured (GROQ_API_KEY and GROQ_VISION_MODELS)")
    content = [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(p).decode()}}
        for p in pictures[:MAX_PICTURES]]
    errors = []
    async with httpx.AsyncClient(timeout=120) as client:
        for provider, key, model in candidates:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
            for _ in range(2):
                try:  # pictures use many tokens: waiting up to a minute for the allowance is better than failing
                    reply = await _call_patiently(client, provider, key, model, messages, temperature, max_wait=70)
                except (LLMError, httpx.HTTPError) as e:
                    errors.append(str(e))
                    log.warning("Picture AI call failed: %s", e)
                    break
                try:
                    return _extract_json(reply), f"{provider}/{model}"
                except (ValueError, json.JSONDecodeError) as e:
                    errors.append(f"{provider}/{model}: bad JSON ({e})")
                    messages += [{"role": "assistant", "content": reply},
                                 {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."}]
    raise LLMError("The picture-reading AI failed: " + " | ".join(errors[-3:]))


# Whisper writes these on silent or music-only videos; they are not real speech
NOT_SPEECH = re.compile(r"^\W*(thank you|thanks for watching|you|bye|music|\[music\]|subtitles by.*)\W*$", re.I)


async def transcribe(data: bytes, filename: str) -> str:
    """What is said in a video (Groq Whisper; files up to 25 MB). Empty when nothing useful is said or it fails."""
    s = get_settings()
    if not s.groq_api_key or not data or len(data) > 25 * 1024 * 1024:
        return ""
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {s.groq_api_key}"},
                files={"file": (filename, data, "video/mp4")},
                data={"model": s.groq_whisper_model, "response_format": "json", "temperature": "0"},
            )
        if r.status_code >= 400:
            log.info("No speech text for %s (%s): %s", filename, r.status_code, r.text[:200])
            return ""
        text = " ".join(str(r.json().get("text") or "").split())
    except (httpx.HTTPError, ValueError) as e:
        log.info("No speech text for %s: %s", filename, e)
        return ""
    return "" if len(text) < 15 or NOT_SPEECH.match(text) else text[:2000]


async def ping() -> str:
    data, used = await ask_json("Reply with JSON only.", 'Return {"ok": true}', temperature=0)
    return used
