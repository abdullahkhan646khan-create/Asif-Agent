"""Keeps every post different from the ones before it.

1. Planner: picks the topic (service, post type, industry), the layout, the picture direction
   (person, place, camera shot, light) and the caption style. Each choice is the one used least
   recently, so the same person, place, layout or style never comes back in the next posts.
2. Copy check: compares a new headline, benefits, captions and picture description with EVERY
   earlier post (including rejected versions). Too similar → the AI must write it again.
3. Picture check: a fingerprint of each picture is compared with earlier pictures.
"""

import io
import random
import re
from difflib import SequenceMatcher

from PIL import Image

from .brand import Brand
from .composer import TEMPLATES

POST_TYPES = {
    "service_promo": "Service promotion: what we do and the result the customer gets",
    "benefits_list": "Benefits list: 3-4 clear reasons to choose this service",
    "problem_solution": "Problem → solution: start from a pain the customer feels today",
    "tip": "Tip / did you know: one useful piece of advice that shows expertise",
    "industry_use": "Industry use case: how one type of business benefits",
    "why_us": "Why Amanasoft: trust, certified engineers, end-to-end service and support",
    "occasion": "Occasion greeting (only when asked): Ramadan, Eid, UAE National Day, GITEX, New Year",
}
AUTO_POST_TYPES = [k for k in POST_TYPES if k != "occasion"]

INDUSTRIES = [
    "clinics and hospitals", "schools and universities", "warehouses and logistics", "retail shops and malls",
    "hotels and restaurants", "real estate and building developers", "corporate offices", "banks and finance",
    "factories and manufacturing", "small businesses and startups",
]

PEOPLE = [
    "an Emirati businessman in a white kandura",
    "an Arab businesswoman wearing a hijab and a dark blazer",
    "a South Asian male engineer in a hard hat and safety vest",
    "a European woman IT manager in smart business attire",
    "a Filipino woman technician in a company polo shirt",
    "a field technician wearing a white safety helmet and a high-visibility vest",
    "a young Arab man in a smart casual shirt",
    "an East Asian woman software developer",
    "a small team of three colleagues of different backgrounds",
    "a mature Indian businessman in a grey suit",
    "no people: a technology or product close-up",
    "no people: architecture or a city view",
]
SETTINGS = [
    "a modern glass office in Downtown Dubai", "a server room or data center", "a warehouse with tall shelves",
    "a retail store", "a clinic reception", "a school or university building", "a hotel lobby",
    "a commercial building under fit-out", "a rooftop overlooking the city", "a co-working space",
    "a factory floor", "a meeting room with a view of Dubai Marina",
]
SHOTS = ["close-up detail shot", "medium shot", "wide establishing shot", "over-the-shoulder shot",
         "low-angle hero shot", "top-down flat lay"]
LIGHTS = ["bright daylight", "golden hour sunlight", "blue hour", "night with city lights", "soft studio lighting"]
CAPTION_STYLES = [
    "open with a question the customer asks themselves",
    "open with a bold one-line statement",
    "a very short story of a customer problem and how it was solved",
    "a '3 signs you need this' list",
    "myth vs fact",
    "a quick practical tip",
    "a before → after contrast",
    "a direct benefit promise",
]


# ---------- planner ----------

def _least_recent(options: list[str], used: list[str | None], rnd: random.Random) -> str:
    """The option used longest ago (never used counts as oldest). `used` is most-recent first."""
    def age(o):
        return used.index(o) if o in used else len(used) + 1
    best = max(age(o) for o in options)
    return rnd.choice([o for o in options if age(o) == best])


def past_plans(history: list[dict]) -> list[dict]:
    return [h["brief"].get("_plan") or {} for h in history if h.get("brief")]


def make_plan(history: list[dict], service: str | None, post_type: str | None, template: str | None,
              brand: Brand, rnd: random.Random | None = None) -> dict:
    """history: earlier posts/versions as {"brief": ..., "captions": ...}, most recent first."""
    rnd = rnd or random.Random()
    plans = past_plans(history)
    used = lambda key: [p.get(key) for p in plans]  # noqa: E731

    if template not in TEMPLATES:
        # never the same layout twice in a row, and prefer the one used longest ago
        recent = [t for t in used("template")[:1] if t]
        template = None
        layout_choices = [t for t in TEMPLATES if t not in recent]
    else:
        layout_choices = [template]

    plan = {
        "service": service if service and brand.service(service) else None,
        "post_type": post_type if post_type in POST_TYPES else _least_recent(AUTO_POST_TYPES, used("post_type"), rnd),
        "industry": _least_recent(INDUSTRIES, used("industry"), rnd),
        "layout_choices": layout_choices,
        "template": template or _least_recent(layout_choices, used("template"), rnd),
        "person": _least_recent(PEOPLE, used("person"), rnd),
        "setting": _least_recent(SETTINGS, used("setting"), rnd),
        "shot": _least_recent(SHOTS, used("shot"), rnd),
        "light": _least_recent(LIGHTS, used("light"), rnd),
        "caption_style": _least_recent(CAPTION_STYLES, used("caption_style"), rnd),
    }
    list_layouts = [t for t in ("checklist", "cards") if t in layout_choices]
    if template is None and plan["post_type"] == "benefits_list" and list_layouts:
        plan["template"] = _least_recent(list_layouts, used("template"), rnd)
    if plan["template"] == "skyline":
        plan["setting"], plan["light"] = "Dubai skyline at night", "night with city lights"
        if plan["shot"] in ("top-down flat lay", "close-up detail shot"):
            plan["shot"] = _least_recent(["wide establishing shot", "medium shot", "over-the-shoulder shot",
                                          "low-angle hero shot"], used("shot"), rnd)
    plan["service_source"] = "user" if plan["service"] else "auto"
    if not plan["service"]:
        plan["service"] = _least_recent(brand.service_names, used("service"), rnd)
    return plan


def plan_label(plan: dict) -> str:
    return f"{plan['service']} · {POST_TYPES[plan['post_type']].split(':')[0]} · for {plan['industry']}"


# ---------- copy check ----------

def _norm(text: str) -> str:
    """Lowercase words only: links, hashtags, phone numbers and emails removed (they repeat on purpose)."""
    text = re.sub(r"https?://\S+|#\w+|\+?\d[\d\s-]{6,}|[\w.]+@[\w.]+", " ", str(text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def _shingles(text: str, n: int = 3) -> set:
    words = _norm(text).split()
    return {" ".join(words[i:i + n]) for i in range(max(len(words) - n + 1, 1))} if words else set()


def _jaccard(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def _ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _first_line(text: str) -> str:
    return next((ln for ln in str(text or "").splitlines() if ln.strip()), "")


def brief_repeats(brief: dict, history: list[dict]) -> list[str]:
    """Problems if the new brief is too close to any earlier post."""
    problems = []
    head = f"{brief.get('headline_top', '')} {brief.get('headline_highlight', '')}"
    mine = {_norm(b) for b in brief.get("benefits") or []}
    for i, h in enumerate(history):
        old = h.get("brief") or {}
        old_head = f"{old.get('headline_top', '')} {old.get('headline_highlight', '')}"
        if _ratio(head, old_head) > 0.8:
            problems.append(f"the headline is almost the same as an earlier post ('{old_head.strip()}'); write a new one")
        if brief.get("subheadline") and _ratio(brief["subheadline"], old.get("subheadline", "")) > 0.8:
            problems.append(f"the subheadline repeats an earlier post ('{old.get('subheadline')}')")
        if i < 15 and len(mine & {_norm(b) for b in old.get("benefits") or []}) >= 2:
            problems.append(f"the benefits repeat a recent post ({', '.join(old.get('benefits'))}); pick different ones")
        if i < 30 and _jaccard(brief.get("image_prompt", ""), old.get("image_prompt", "")) > 0.3:
            problems.append("the image_prompt describes almost the same picture as a recent post; change the scene")
    return list(dict.fromkeys(problems))[:4]


def captions_repeat(captions: dict, history: list[dict]) -> list[str]:
    problems = []
    hook = _first_line(captions.get("facebook"))
    for h in history:
        old = h.get("captions") or {}
        if _jaccard(captions.get("facebook", ""), old.get("facebook", "")) > 0.3:
            problems.append("the Facebook caption repeats a lot of wording from an earlier post; rewrite it")
        if _ratio(hook, _first_line(old.get("facebook"))) > 0.75:
            problems.append(f"the Facebook opening line is like an earlier one ('{_first_line(old.get('facebook'))[:60]}')")
        if _ratio(captions.get("x", ""), old.get("x", "")) > 0.75:
            problems.append("the X caption is almost the same as an earlier one")
        if _ratio(captions.get("threads", ""), old.get("threads", "")) > 0.75:
            problems.append("the Threads caption is almost the same as an earlier one")
    return list(dict.fromkeys(problems))[:4]


def recent_openings(history: list[dict], n: int = 12) -> list[str]:
    return [_first_line((h.get("captions") or {}).get("facebook"))[:80] for h in history[:n] if h.get("captions")]


# ---------- picture check ----------

def fingerprint(image_bytes: bytes) -> str:
    """64-bit difference hash: near-identical pictures get near-identical fingerprints."""
    img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(img.tobytes())
    bits = "".join("1" if px[r * 9 + c] > px[r * 9 + c + 1] else "0" for r in range(8) for c in range(8))
    return f"{int(bits, 2):016x}"


def looks_like_earlier(fp: str, history: list[dict], max_distance: int = 8) -> bool:
    for h in history[:40]:
        old = (h.get("brief") or {}).get("_fingerprint")
        if old and bin(int(fp, 16) ^ int(old, 16)).count("1") <= max_distance:
            return True
    return False
