"""The prompt assistant in the dashboard's chat bubble.

The client types an idea (any words, any language); the assistant answers with ready-to-use options for the
Create post form: a Prompt (what the picture shows), a Message (the exact words on the post) and a Service.
It only writes text; nothing is created or published from the chat.
"""

import re

from .brand import load_brand
from .llm import ask_json
from .writer import _trim, unsupported_claims

MAX_MESSAGES = 12        # the conversation the AI sees (the newest ones)
MAX_CHARS = 1500         # per message
MESSAGE_LIMIT = 150      # the Message box on the dashboard


def _system(recent: list[str]) -> str:
    brand = load_brand()
    c = brand.company
    services = "\n".join(f"- {s['name']}: {s['one_liner']}" for s in brand.profile["services"])
    avoid = "\n".join(f"- {r}" for r in recent[:15]) or "- (none yet)"
    return f"""You are Amana, the friendly creative assistant inside {brand.name}'s Post Agent dashboard.
{brand.name} is {c['type']} in {c['location']}. You help the user write the inputs of the "Create post" form:
- prompt: what the PICTURE shows. 30-80 words, one paragraph: the subject, the setting (modern Dubai/UAE places
  when it fits), the people (what they do and wear; on technical sites they wear white safety helmets and
  professional workwear), the camera angle, the light and the mood. Ultra-realistic premium photography.
  Never ask for text, logos, signs, flags or watermarks in the picture.
- message: the exact words printed on the post. 1-2 short sentences, at most 90 characters: a strong headline,
  optionally followed by a short second sentence. No phone numbers, prices, discounts, guarantees, awards, "100%"
  or invented numbers.
- service: the exact name of the {brand.name} service it is about, from the list below, or "" for Auto.

SERVICES
{services}

HOW TO ANSWER
- When the user gives an idea (even a rough one, in any language), give 3 clearly different options: a different
  scene, person, angle and light each, and a different message. Make them fresh and specific, never generic.
- When the user asks to change something ("shorter", "more luxury", "for a clinic", "another one"), use the
  conversation so far and give new options with that change.
- When the user only greets you or asks a question, reply briefly and helpfully, with no options or one example.
- reply: 1-2 friendly, simple English sentences. The user may write in English, Urdu, Hindi or Arabic: understand
  it anyway, and always write prompt and message in English.
- Make every option different from these recent posts:
{avoid}

Reply with ONLY this JSON object:
{{"reply": "...", "options": [{{"title": "2-5 words naming the idea", "prompt": "...", "message": "...",
  "service": "exact service name or empty"}}]}}"""


def _check(d: dict) -> list[str]:
    p = []
    if not str(d.get("reply") or "").strip():
        p.append("'reply' is missing")
    options = d.get("options") or []
    if not isinstance(options, list):
        return p + ["'options' must be a list"]
    brand = load_brand()
    for n, o in enumerate(options[:3], 1):
        if not isinstance(o, dict):
            p.append(f"option {n} must be an object")
            continue
        words = len(str(o.get("prompt") or "").split())
        if words < 20:
            p.append(f"option {n}: the prompt is too short ({words} words); write 30-80 words")
        if len(str(o.get("message") or "")) > MESSAGE_LIMIT:
            p.append(f"option {n}: the message is too long; at most 90 characters")
        claims = unsupported_claims([o.get("message")])
        if claims:
            p.append(f"option {n}: remove {claims} from the message (no guarantees, awards or invented numbers)")
        svc = str(o.get("service") or "")
        if svc and not brand.service(svc):
            p.append(f"option {n}: service '{svc}' is not in the list; use an exact name or empty")
    return p


def _clean_message(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip().strip('"“”')
    return _trim(text, MESSAGE_LIMIT)


async def reply(messages: list[dict], recent: list[str]) -> dict:
    """messages: [{"role": "user" | "assistant", "content": "..."}], oldest first, the last one from the user."""
    talk = [m for m in messages if m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()]
    talk = talk[-MAX_MESSAGES:]
    lines = [f"{'USER' if m['role'] == 'user' else 'AMANA'}: {str(m['content'])[:MAX_CHARS]}" for m in talk[:-1]]
    user = (("CONVERSATION SO FAR:\n" + "\n".join(lines) + "\n\n") if lines else "") + \
        f"THE USER'S NEW MESSAGE: {str(talk[-1]['content'])[:MAX_CHARS]}"
    data, used = await ask_json(_system(recent), user, _check, temperature=0.9)
    brand = load_brand()
    options = []
    for o in (data.get("options") or [])[:3]:
        if not isinstance(o, dict) or not str(o.get("prompt") or "").strip():
            continue
        svc = str(o.get("service") or "")
        options.append({
            "title": " ".join(str(o.get("title") or "Idea").split())[:60],
            "prompt": " ".join(str(o["prompt"]).split())[:900],
            "message": _clean_message(o.get("message")),
            "service": brand.service(svc)["name"] if svc and brand.service(svc) else "",
        })
    return {"reply": str(data.get("reply") or "Here you go!").strip()[:600], "options": options, "model": used}
