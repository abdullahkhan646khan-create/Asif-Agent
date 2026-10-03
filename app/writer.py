"""Step 1 (creative director) and step 2 (copywriter): idea → brief → captions."""

import json
import re

from .brand import Brand
from .composer import TEMPLATES
from .llm import ask_json
from .variety import POST_TYPES, brief_repeats, captions_repeat, recent_openings

LAYOUTS = {
    "wave": "Promote ONE service with a clear solution message. Headline top-left on a blue gradient, the photo in "
            "the lower-right under a curved wave, a strip with 3 benefits at the bottom. Photo composition: main "
            "subject in the right half and lower half, clean and uncluttered.",
    "spotlight": "Bold problem → solution statements: security, AI, networks and other tech themes. The photo is the "
                 "full background with a dark fade at the top for the headline, 3 benefits at the bottom. Photo "
                 "composition: dramatic, the top third is calm and dark (night sky, ceiling or dark space), main "
                 "subject in the lower middle.",
    "checklist": "Benefit lists, reasons to choose, features or tips with 3-4 short points. Text on a light panel on "
                 "the left, photo in a tall panel on the right. Photo composition: the whole subject, including any "
                 "device or object the person uses, fits inside the central vertical third of the image (a tall "
                 "narrow strip); the left and right thirds are only simple background.",
    "skyline": "Premium tall poster like a magazine ad: Dubai at night under a starry sky with a UAE flag, a big "
               "headline, a short benefit paragraph and 3 benefits. Best for AI and smart-technology posts, "
               "big-picture vision messages, brand awareness and UAE occasions. Photo composition: Dubai skyline at "
               "night under a starry deep-blue sky; the top third is dark night sky; the technology subject (glowing "
               "light streams, servers, a person at work or a device) sits in the middle; the bottom quarter is "
               "darker and calm.",
}

# Promises the brand can't back up: guarantees, perfection, awards, unconfirmed counts ("20 years", "500 clients").
UNSUPPORTED = re.compile(
    r"guarantee\w*|\b100\s?%|\bzero[\s‑-]?(?:downtime|risk|errors?)\b|\baward\w*|\b#1\b|\bnumber one\b|\bno\.?\s?1\b"
    r"|\b\d[\d,]*\+?\s*(?:years?|clients?|projects?|customers?|companies)\b", re.I)


def unsupported_claims(texts) -> list[str]:
    found = []
    for t in texts:
        found += [m.group(0) for m in UNSUPPORTED.finditer(str(t or ""))]
    return list(dict.fromkeys(found))


LIMITS = {"headline_top": 32, "headline_highlight": 18, "subheadline": 80, "body": 190, "benefit": 20, "bullet": 38}
NEEDS_BENEFITS = ("wave", "spotlight", "skyline")


# ---------- step 1: creative director ----------

def _director_system(brand: Brand, focus: str | None = None) -> str:
    v = brand.profile["visual"]
    layouts = "\n".join(f"- {k}: {d}" for k, d in LAYOUTS.items())
    look = "\n".join(f"- {x}" for x in v["look_and_feel"])
    return f"""You are the senior creative director for {brand.name}'s social media.
You turn a short idea into a precise creative brief for ONE high-quality social media post (Facebook, X and Threads).

BRAND KIT
{brand.context_text(all_services=True, focus=focus)}

VISUAL STYLE
{look}
- Photo style: {v['photo_style_prompt']}

LAYOUTS (pick the one that fits the message best, and vary it from the recent posts)
{layouts}

CONTENT FOCUS (most important)
Every post shows the customer's problem or goal, how {brand.name} solves it, and concrete benefits.
- headline_top + headline_highlight = the result the customer wants, read as one headline. Examples:
  "Never miss" + "A Call", "Protect your business from" + "Cyber Threats", "See everything," + "Stop Threats",
  "Accelerate your business with" + "AI-Driven Networks". headline_top max {LIMITS['headline_top']} characters,
  headline_highlight (shown big) max {LIMITS['headline_highlight']} characters.
- subheadline: how {brand.name} delivers it and for whom, max {LIMITS['subheadline']} characters.
- benefits: exactly 3 concrete benefits of 2-3 words each, max {LIMITS['benefit']} characters each
  (e.g. "24/7 monitoring", "Remote viewing", "Lower costs"). Never vague words like "Quality" or "Best service".
- bullets: only for the checklist layout, 3-4 points of max {LIMITS['bullet']} characters; otherwise [].
- body: only for the skyline layout, 1-2 sentences of max {LIMITS['body']} characters that explain the solution
  and its benefits; otherwise "".
- No calls to action on the image ("Book a demo", "Call now" and similar belong in the captions only).
- Only use facts from the brand kit or the idea. Never invent numbers, prices, discounts, client names, awards
  or guarantees.
- image_prompt: 60-120 words describing a realistic, premium photo that fits the service and the chosen layout's
  composition. Describe subject, setting (modern Dubai/UAE business settings when it fits), people (professional
  attire, safety gear on site), lighting and colour grading in brand tones. Never ask for text, logos, flags,
  screens with readable writing, or watermarks (the designer adds the UAE flag on the skyline layout itself).

Reply with ONLY this JSON object:
{{"post_type": "service_promo | benefits_list | problem_solution | tip | industry_use | greeting | brand_awareness",
  "service": "<exact service name from the list, or General>",
  "angle": "<one sentence: the customer problem, our solution and the main benefit>",
  "template": "wave | spotlight | checklist | skyline",
  "headline_top": "...", "headline_highlight": "...", "subheadline": "...", "body": "...",
  "benefits": ["...", "...", "..."], "bullets": [], "image_prompt": "..."}}"""


def _director_user(idea: str, plan: dict, history: list[dict], feedback: dict | None) -> str:
    parts = [f"IDEA: {idea}" if idea else
             "IDEA: (none given) Create a fresh post from the topic below."]
    if plan["service_source"] == "user" or not idea:
        service = plan["service"]
    else:
        service = f"the one the idea is about; only if the idea is general, use {plan['service']}"
    parts.append(
        f"SERVICE: {service}"
        + f"\nPOST TYPE: {POST_TYPES[plan['post_type']]}"
        + f"\nAUDIENCE FOCUS: {plan['industry']} (use it when it fits the idea)"
        + f"\nLAYOUT: use '{plan['template']}'."
    )
    place = "Dubai at night under a starry sky" if plan["template"] == "skyline" else plan["setting"]
    parts.append(
        "VISUAL DIRECTION for image_prompt (must follow; adapt it naturally to the service):\n"
        f"- who: {plan['person']}\n- where: {place}\n- camera: {plan['shot']}\n- light: {plan['light']}"
    )
    recent = [h["brief"] for h in history[:15] if h.get("brief")]
    if recent:
        lines = [f"- [{b.get('template', '?')}] {b.get('service', '?')}: {b.get('headline_top', '')} "
                 f"{b.get('headline_highlight', '')} | benefits: {', '.join(b.get('benefits') or [])}" for b in recent]
        parts.append("RECENT POSTS - never repeat these headlines, angles or benefits:\n" + "\n".join(lines))
    if feedback:
        parts.append(
            "THE PREVIOUS VERSION WAS REJECTED. Make a clearly different version.\n"
            f"Reason given: {feedback.get('reason') or '(no reason given)'}\n"
            f"Previous brief: {json.dumps(feedback.get('brief') or {}, ensure_ascii=False)}"
        )
    return "\n\n".join(parts)


def _check_brief(brand: Brand):
    def check(d: dict) -> list[str]:
        p = []
        t = d.get("template")
        if t not in TEMPLATES:
            p.append(f"'template' must be one of {', '.join(TEMPLATES)}")
        for k in ("headline_top", "headline_highlight", "image_prompt"):
            if not str(d.get(k) or "").strip():
                p.append(f"'{k}' is missing")
        if t in ("wave", "spotlight") and not str(d.get("subheadline") or "").strip():
            p.append("'subheadline' is missing")
        if t == "skyline" and not str(d.get("body") or "").strip():
            p.append("the skyline layout needs 'body'")
        for k in ("headline_top", "headline_highlight", "subheadline", "body"):
            if len(str(d.get(k) or "")) > LIMITS[k]:
                p.append(f"'{k}' is {len(str(d[k]))} characters; max {LIMITS[k]}")
        benefits = _as_list(d.get("benefits"))
        if t in NEEDS_BENEFITS and len(benefits) != 3:
            p.append("give exactly 3 benefits")
        for b in benefits:
            if len(str(b)) > LIMITS["benefit"]:
                p.append(f"benefit '{b}' is too long; max {LIMITS['benefit']} characters")
        bullets = _as_list(d.get("bullets"))
        if t == "checklist" and not 3 <= len(bullets) <= 4:
            p.append("checklist layout needs 3-4 bullets")
        for b in bullets:
            if len(str(b)) > LIMITS["bullet"]:
                p.append(f"bullet '{b}' is too long; max {LIMITS['bullet']} characters")
        if len(str(d.get("image_prompt") or "").split()) < 30:
            p.append("image_prompt is too short; write 60-120 words")
        svc = d.get("service")
        if svc and svc != "General" and not brand.service(svc):
            p.append(f"service '{svc}' is not in the list; use an exact name or General")
        claims = unsupported_claims([d.get(k) for k in ("headline_top", "headline_highlight", "subheadline", "body")]
                                    + _as_list(d.get("benefits")) + _as_list(d.get("bullets")))
        if claims:
            p.append(f"remove unsupported claims {claims}: no guarantees, awards or numbers that are not in the brand kit")
        return p
    return check


def _as_list(value) -> list[str]:
    """The AI sometimes returns a list as one text ("a, b, c"); always get a real list."""
    if isinstance(value, str):
        value = re.split(r"[\n;,•]+", value)
    return [str(v).strip() for v in (value or []) if str(v).strip()]


def _trim(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-")


def _clean_brief(d: dict, brand: Brand, template_pref: str | None) -> dict:
    d = dict(d)
    d.pop("cta", None)
    if template_pref in TEMPLATES:
        d["template"] = template_pref
    if d.get("template") not in TEMPLATES:
        d["template"] = "wave"
    for k in ("headline_top", "headline_highlight", "subheadline", "body"):
        d[k] = _trim(d.get(k), LIMITS[k])
    if not d.get("service") or (d["service"] != "General" and not brand.service(d["service"])):
        d["service"] = "General"
    svc = brand.service(d["service"])
    pool = svc["selling_points"] if svc else ["Certified engineers", "End-to-end service", "Long-term support"]

    benefits = [_trim(b, LIMITS["benefit"]) for b in _as_list(d.get("benefits"))][:3]
    if d["template"] in NEEDS_BENEFITS and len(benefits) < 3:
        extra = [b for b in pool if len(b) <= LIMITS["benefit"] and b not in benefits]
        benefits = (benefits + extra)[:3]
    d["benefits"] = benefits

    bullets = [_trim(b, LIMITS["bullet"]) for b in _as_list(d.get("bullets"))][:4]
    if d["template"] == "checklist" and len(bullets) < 3:
        bullets = (bullets + [b for b in pool if b not in bullets])[:4]
    d["bullets"] = bullets
    if d["template"] == "skyline" and not d.get("body"):
        d["body"] = d.get("subheadline", "")
    return d


async def make_brief(brand: Brand, idea: str, plan: dict, history: list[dict],
                     feedback: dict | None = None) -> dict:
    """Write the brief; if it repeats an earlier post, write it again (up to 3 rounds)."""
    base_check = _check_brief(brand)
    user = _director_user(idea, plan, history, feedback)
    # the planned service's details only help when it is really this post's service
    focus = plan["service"] if plan["service_source"] == "user" or not idea else None
    brief, used, problems = None, "", []
    for round_no in range(3):
        def check(d):
            # judge the answer by the layout the planner chose (the one it will be drawn with)
            return base_check({**d, "template": plan["template"]}) or \
                brief_repeats(_clean_brief(d, brand, plan["template"]), history)
        extra = ("\n\nYOUR LAST TRY REPEATED EARLIER POSTS:\n- " + "\n- ".join(problems)) if problems else ""
        data, used = await ask_json(_director_system(brand, focus), user + extra, check,
                                    temperature=0.85 + 0.05 * round_no)
        brief = _clean_brief(data, brand, plan["template"])
        problems = brief_repeats(brief, history)
        if not problems:
            break
    brief["_model"] = used
    brief["_plan"] = {**plan, "service": brief["service"] if brief["service"] != "General" else plan["service"]}
    if problems:
        brief["_variety_warning"] = problems
    return brief


def image_prompt(brand: Brand, brief: dict) -> str:
    v = brand.profile["visual"]
    composition = LAYOUTS[brief["template"]].split("Photo composition:")[1].strip()
    avoid = ", ".join(v["avoid_in_images"])
    shape = "portrait 4:5" if brief["template"] == "skyline" else "square 1:1"
    return (
        f"Generate a photo. {brief['image_prompt'].strip()}\n\n"
        f"Style: {v['photo_style_prompt']}.\n"
        f"Composition: {composition}\n"
        f"Format: {shape}, high resolution.\n"
        f"Strictly avoid: {avoid}. The image must contain absolutely NO text, letters, numbers, words, logos, "
        "labels, signs, flags or watermarks anywhere. Create the image now, do not ask questions."
    )


# ---------- step 2: copywriter ----------

URL_RE = re.compile(r"https?://\S+|\b[\w-]+\.(?:ae|com|net|org|io|co)\b\S*", re.I)


def x_length(text: str) -> int:
    """Approximate X's weighted count: links count 23, emoji and wide characters count 2."""
    text = URL_RE.sub("x" * 23, text)
    return sum(2 if ord(ch) > 0x2000 else 1 for ch in text)


def _copy_system(brand: Brand, service: str | None = None) -> str:
    c = brand.company
    tags = brand.profile["hashtags"]
    pool = "; ".join(f"{k}: {' '.join(v)}" for k, v in tags["by_category"].items())
    return f"""You are the social media copywriter for {brand.name}. Write the captions for ONE post from a creative brief.

BRAND KIT
{brand.context_text(all_services=False, focus=service)}

PLATFORM RULES
- facebook: 60-150 words. Strong first-line hook. 2-3 short paragraphs or a short ✅ list. End with the call to action
  using the exact contact details: 📞 {c['phone']} · 🌐 {c['website']} · ✉️ {c['email']}. Last line: 4-7 hashtags.
  At most 3 emojis besides the contact icons.
- x: MAXIMUM 240 characters in total including hashtags and the website. One punchy line, the benefit, {c['website']},
  and 1-2 hashtags. At most 1 emoji.
- threads: MAXIMUM 450 characters. Conversational, 2-4 short lines, ends with the call to action. No hashtags.
- threads_topic: ONE topic for Threads, 1-3 words, no '#', no '&' or '.', max 40 characters.
- title: short internal title for the approval email, max 60 characters.
- Hashtags always include {' '.join(tags['always'])}; pick the rest from: {pool}.
- Contact details must be written exactly as above. Never write amanasoft.net.
- Only use facts from the brand kit and the brief. Never invent numbers, prices, discounts, client names or awards.

Reply with ONLY this JSON object:
{{"title": "...", "facebook": "...", "x": "...", "threads": "...", "threads_topic": "..."}}"""


def _check_copy(brand: Brand):
    phone_digits = re.sub(r"\D", "", brand.company["phone"])

    def check(d: dict) -> list[str]:
        p = []
        for k in ("title", "facebook", "x", "threads"):
            if not str(d.get(k) or "").strip():
                p.append(f"'{k}' is missing")
        x_len = x_length(str(d.get("x") or ""))
        if x_len > 270:
            p.append(f"'x' is about {x_len} characters on X; it must be under 240. Make it shorter.")
        threads_len = len(str(d.get("threads") or ""))
        if threads_len > 480:
            p.append(f"'threads' is {threads_len} characters; max 450")
        all_text = " ".join(str(d.get(k) or "") for k in ("facebook", "x", "threads"))
        if "amanasoft.net" in all_text.lower():
            p.append("the website is amanasoft.ae, not amanasoft.net")
        for m in re.findall(r"\+?971[\d\s-]{6,}", all_text):
            if re.sub(r"\D", "", m) != phone_digits:
                p.append(f"wrong phone number '{m.strip()}'; the only number is {brand.company['phone']}")
        claims = unsupported_claims([all_text])
        if claims:
            p.append(f"remove unsupported claims {claims}: no guarantees, awards or numbers that are not in the brand kit")
        return p
    return check


def _fit_x(text: str) -> str:
    """Last resort if X text is still too long: drop extra hashtags, then whole sentences, then words."""
    text = text.strip()
    while x_length(text) > 275:
        tags = re.findall(r"#\w+", text)
        if len(tags) > 1:
            text = text[::-1].replace(tags[-1][::-1], "", 1)[::-1].strip()
            continue
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 2:
            sentences.pop(-2)
            text = " ".join(sentences)
            continue
        break
    if x_length(text) > 275:
        words = text.split()
        while len(words) > 1 and x_length(" ".join(words) + "…") > 275:
            words.pop()
        text = " ".join(words) + "…"
        while x_length(text) > 275:  # one giant "word": cut characters
            text = text[:-2] + "…"
    return re.sub(r"[ \t]+", " ", text)


async def make_copy(brand: Brand, idea: str, brief: dict, plan: dict, history: list[dict],
                    feedback: dict | None = None) -> dict:
    public_brief = {k: v for k, v in brief.items() if not k.startswith("_") and k != "image_prompt"}
    user = f"IDEA: {idea or '(none, use the brief)'}\n\nCREATIVE BRIEF: {json.dumps(public_brief, ensure_ascii=False)}"
    user += f"\n\nCAPTION STYLE for this post: {plan['caption_style']}."
    openings = [o for o in recent_openings(history) if o]
    if openings:
        user += "\nDo NOT start the Facebook caption like any of these recent openings:\n- " + "\n- ".join(openings)
    tags = [" ".join(re.findall(r"#\w+", (h.get("captions") or {}).get("facebook", ""))) for h in history[:5]]
    if any(tags):
        user += "\nRecent hashtag sets (use a different mix): " + " | ".join(t for t in tags if t)
    if feedback and feedback.get("reason"):
        user += f"\n\nThe previous version was rejected. Reason: {feedback['reason']}. Write clearly different captions."

    base_check = _check_copy(brand)
    data, used, problems = {}, "", []
    for round_no in range(3):
        extra = ("\n\nYOUR LAST TRY REPEATED EARLIER POSTS:\n- " + "\n- ".join(problems)) if problems else ""
        data, used = await ask_json(_copy_system(brand, brief.get("service")), user + extra,
                                    lambda d: base_check(d) or captions_repeat(d, history),
                                    temperature=0.75 + 0.05 * round_no)
        problems = captions_repeat(data, history)
        if not problems:
            break
    out = {k: str(data.get(k) or "").strip() for k in ("title", "facebook", "x", "threads", "threads_topic")}
    out["x"] = _fit_x(out["x"])
    if len(out["threads"]) > 490:
        out["threads"] = out["threads"][:480].rsplit(" ", 1)[0] + "…"
    topic = re.sub(r"[#&.]", "", out["threads_topic"]).strip()[:40]
    out["threads_topic"] = topic
    out["title"] = out["title"][:80] or f"{brief['headline_top']} {brief['headline_highlight']}"
    out["_model"] = used
    if problems:
        out["_variety_warning"] = problems
    return out
