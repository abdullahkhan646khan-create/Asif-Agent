"""Step 1 (creative director) and step 2 (copywriter): idea → brief → captions."""

import json
import re

from .brand import Brand
from .composer import TEMPLATES
from .config import get_settings
from .llm import LLMError, _extract_json, ask_json, ask_vision_json
from .variety import POST_TYPES, brief_repeats, captions_repeat, recent_openings

LAYOUTS = {
    "wave": "Promote ONE service with a clear solution message. Headline top-left on a blue gradient, the photo in "
            "the lower-right under a curved wave, a strip with 3 benefits at the bottom. Photo composition: main "
            "subject in the right half and lower half, clean and uncluttered.",
    "spotlight": "Bold problem → solution statements: security, AI, networks and other tech themes. The photo is the "
                 "full background with a dark fade at the top for the headline, 3 benefits at the bottom. Photo "
                 "composition: dramatic, the top third is calm and dark (night sky, ceiling or dark space), main "
                 "subject in the lower middle.",
    "skyline": "Premium tall poster like a magazine ad: Dubai at night under a starry sky with a UAE flag, a big "
               "headline, a short benefit paragraph and 3 benefits. Best for AI and smart-technology posts, "
               "big-picture vision messages, brand awareness and UAE occasions. Photo composition: Dubai skyline at "
               "night under a starry deep-blue sky; the top third is dark night sky; the technology subject (glowing "
               "light streams, servers, a person at work or a device) sits in the middle; the bottom quarter is "
               "darker and calm.",
    "split": "Strong service statement with 3 numbered benefits: a dark navy panel on the right holds the headline, "
             "a short subheadline and the benefits as a numbered list; the photo fills a tall panel on the left with "
             "a diagonal edge. Good for infrastructure, cabling, networks, security and why-choose-us posts. Photo "
             "composition: the main subject (person or equipment) is in the central vertical third of the image and "
             "fills it from top to bottom; the left and right thirds are only simple background.",
    "cards": "Tall post: the photo on top, 3 benefit cards with check icons across its lower edge, the headline and "
             "subheadline centred below on a light background. Good for benefits, features and service promotion. "
             "Photo composition: the main subject is in the middle of the image with clear space above the head; "
             "the bottom quarter is simple background such as floor or desk (the benefit cards cover it).",
    "frame": "Clean editorial look: the photo fills the post and a white card at the bottom carries the headline, "
             "subheadline and 3 benefit tags. Good for industry use cases, tips and customer-focused messages. Photo "
             "composition: the main subject is in the upper half of the image; the lower half is calm and simple "
             "(desk, floor, wall or a soft blurred background), because a white card covers it.",
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


LIMITS = {"headline_top": 32, "headline_highlight": 18, "subheadline": 80, "body": 190, "benefit": 20}
# the AI is asked for a little less than the real limit, so it rarely goes over and nothing has to be cut
AIM = {**LIMITS, "headline_top": 30, "subheadline": 70, "body": 170}
NEEDS_BENEFITS = ("wave", "spotlight", "skyline", "split", "cards", "frame")
NEEDS_SUBHEADLINE = ("wave", "spotlight", "split", "cards", "frame")


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
  "Accelerate your business with" + "AI-Driven Networks". headline_top max {AIM['headline_top']} characters,
  headline_highlight (shown big) max {AIM['headline_highlight']} characters.
- subheadline: how {brand.name} delivers it and for whom, max {AIM['subheadline']} characters.
- benefits: exactly 3 concrete benefits of 2-3 words each, max {AIM['benefit']} characters each
  (e.g. "24/7 monitoring", "Remote viewing", "Lower costs"). Never vague words like "Quality" or "Best service".
  Every benefit and headline must be natural, grammatical English ("Replies in seconds", never
  "Seconds replies").
- body: only for the skyline layout, 1-2 sentences of max {AIM['body']} characters that explain the solution
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
  "template": "{' | '.join(TEMPLATES)}",
  "headline_top": "...", "headline_highlight": "...", "subheadline": "...", "body": "...",
  "benefits": ["...", "...", "..."], "image_prompt": "..."}}"""


def _director_user(idea: str, plan: dict, history: list[dict], feedback: dict | None) -> str:
    parts = [f"IDEA: {idea}" if idea else
             "IDEA: (none given) Create a fresh post from the topic below."]
    if plan["service_source"] == "user" or not idea:
        service = plan["service"]
    else:
        service = f"the one the idea is about; only if the idea is general, use {plan['service']}"
    parts.append(
        f"SERVICE: {service}"
        + f"\nPOST TYPE: {POST_TYPES[plan['post_type']]}" + (" (only if it fits the idea)" if idea else "")
        + f"\nAUDIENCE FOCUS: {plan['industry']} (use it only if the idea does not name an audience)"
        + f"\nLAYOUT: use '{plan['template']}'."
    )
    place = "Dubai at night under a starry sky" if plan["template"] == "skyline" else plan["setting"]
    if idea:  # the client's idea decides the picture; the plan only fills what the idea leaves open
        parts.append(
            "THE IDEA WINS: the headline, the picture and the people must match the idea exactly. Use these only for "
            "what the idea does not say:\n"
            f"- who: {plan['person']}\n- where: {place}\n- camera: {plan['shot']}\n- light: {plan['light']}"
        )
    else:
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
        if t in NEEDS_SUBHEADLINE and not str(d.get("subheadline") or "").strip():
            p.append("'subheadline' is missing")
        if t == "skyline" and not str(d.get("body") or "").strip():
            p.append("the skyline layout needs 'body'")
        for k in ("headline_top", "headline_highlight", "subheadline", "body"):
            if len(str(d.get(k) or "")) > LIMITS[k]:
                p.append(f"'{k}' is {len(str(d[k]))} characters; rewrite it shorter (max {AIM[k]}) as a complete phrase")
        benefits = _as_list(d.get("benefits"))
        if t in NEEDS_BENEFITS and len(benefits) != 3:
            p.append("give exactly 3 benefits")
        for b in benefits:
            if len(str(b)) > LIMITS["benefit"]:
                p.append(f"benefit '{b}' is too long; max {AIM['benefit']} characters")
        if len(str(d.get("image_prompt") or "").split()) < 30:
            p.append("image_prompt is too short; write 60-120 words")
        svc = d.get("service")
        if svc and svc != "General" and not brand.service(svc):
            p.append(f"service '{svc}' is not in the list; use an exact name or General")
        claims = unsupported_claims([d.get(k) for k in ("headline_top", "headline_highlight", "subheadline", "body")]
                                    + _as_list(d.get("benefits")))
        if claims:
            p.append(f"remove unsupported claims {claims}: no guarantees, awards or numbers that are not in the brand kit")
        return p
    return check


def _as_list(value) -> list[str]:
    """The AI sometimes returns a list as one text ("a, b, c"); always get a real list."""
    if isinstance(value, str):
        value = re.split(r"[\n;,•]+", value)
    return [str(v).strip() for v in (value or []) if str(v).strip()]


# words a cut phrase must not end with ("…so developers can focus on")
DANGLING = {"a", "an", "the", "and", "or", "but", "so", "to", "for", "of", "on", "in", "at", "by", "with", "from",
            "into", "your", "our", "their", "its", "that", "which", "can", "every", "each", "all", "any", "this",
            "these", "more", "most", "as", "than", "is", "are", "will", "while", "&", "-", "–", "—"}


def _trim(text: str, limit: int) -> str:
    """Shorten to the limit without ending mid-thought: cut at a comma, dash or full stop when one is near the
    end, otherwise at a word, and never leave a dangling word like 'on' or 'and' at the end."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    head = text[:limit + 1]
    breaks = [m.start() for m in re.finditer(r"[.;:!?](?=\s)|,(?=\s)|\s[–—-]\s", head)]
    good = [b for b in breaks if b >= limit * 0.4]
    if good:
        cut = head[:good[-1]]
    else:
        cut = head[:limit].rsplit(" ", 1)[0] if " " in head[:limit] else head[:limit]
    words = cut.split()
    while len(words) > 1 and words[-1].lower().strip(",;:") in DANGLING:
        words.pop()
    return " ".join(words).rstrip(",;:–—- ")


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

    d.pop("bullets", None)  # only the removed checklist layout used bullet points
    if d["template"] == "skyline" and not d.get("body"):
        d["body"] = d.get("subheadline", "")
    return d


async def make_brief(brand: Brand, idea: str, plan: dict, history: list[dict],
                     feedback: dict | None = None, message: str = "") -> dict:
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
    return use_message(brief, message) if message.strip() else brief


CLEAN_LIMITS = {"headline_top": 40, "headline_highlight": 24, "subheadline": 72}
CLEAN_STYLE = ("ultra-realistic premium commercial photography, natural cinematic light, rich true-to-life colours, "
               "sharp focus, shallow depth of field")
CLEAN_COMPOSITION = ("portrait 4:5 frame; the main subject is clear and beautifully framed; the lower third is calmer "
                     "(floor, road, table, water or a soft background) because a short headline sits there")


def _clean_system(brand: Brand, industry: str | None) -> str:
    services = ", ".join(brand.service_names)
    if industry:
        who = (f"You are a senior creative director making a DEMO post for a prospective client in the {industry} "
               f"industry. It shows them what their social media could look like. The post promotes a typical premium "
               f"{industry} business in Dubai (never a real company or brand name).")
        service_rule = 'service: always "General".'
    else:
        who = (f"You are the senior creative director for {brand.name}'s social media. Turn the client's idea into ONE "
               "premium post: a stunning photo with a short text on it.\nTHE IDEA IS THE BRIEF. Follow it exactly: its "
               "subject, scene, objects, people, place and mood. Never change the topic or swap in something else. If "
               "the idea is a detailed image description, keep every detail of it in image_prompt.")
        service_rule = f'service: if the idea is about one of these services use its exact name, otherwise "General": {services}.'
    return f"""{who}

ON-IMAGE TEXT (short and elegant):
- headline_top: the main message, 2-6 words, max {CLEAN_LIMITS['headline_top']} characters.
- headline_highlight: 1-3 words shown in an accent colour right after it; may be "".
- subheadline: one short supporting line, max {CLEAN_LIMITS['subheadline']} characters; may be "".
- No calls to action, prices, phone numbers, websites or invented facts (no numbers, awards or guarantees).
- Natural, grammatical English.

image_prompt: 70-140 words describing an ultra-realistic premium commercial photograph: subject, setting (Dubai/UAE
when it fits), composition (portrait 4:5, the main subject clear, the lower third calmer for the text), light and mood.
Never ask for text, logos, brand badges, signs, flags or watermarks.
{service_rule}

Reply with ONLY this JSON object:
{{"service": "...", "angle": "<one sentence: what the post shows and why it appeals>", "headline_top": "...",
  "headline_highlight": "...", "subheadline": "...", "image_prompt": "..."}}"""


def split_message(message: str) -> tuple[str, str]:
    """The client's exact words for the picture → (headline, smaller line). Never reworded: a short message is the
    headline; a longer one is split at its first line break or sentence end."""
    text = re.sub(r"[ \t]+", " ", str(message or "")).strip()
    if "\n" in text:
        head, rest = text.split("\n", 1)
        return head.strip(), " ".join(rest.split())
    m = re.search(r"(?<=[.!?:])\s+|\s[–—-]\s", text)
    if m and m.start() <= 60 and text[m.end():].strip():
        return text[:m.start()].strip(), text[m.end():].strip()   # "Arrive in style." + "Book your chauffeur today."
    if len(text) <= 45:
        return text, ""
    cut = text[:60].rfind(", ")
    if cut >= 20:  # keep the comma with the headline: the client's text stays exact
        return text[:cut + 1].strip(), text[cut + 1:].strip()
    cut = text[:50].rfind(" ")
    return text[:cut].strip(), text[cut:].strip()


def use_message(brief: dict, message: str) -> dict:
    """Put the client's message on the picture, word for word."""
    head, line = split_message(message)
    return {**brief, "headline_top": head, "headline_highlight": "", "subheadline": line, "_message": message.strip()}


def _check_clean(brand: Brand):
    def check(d: dict) -> list[str]:
        p = []
        for k in ("headline_top", "image_prompt"):
            if not str(d.get(k) or "").strip():
                p.append(f"'{k}' is missing")
        for k, limit in CLEAN_LIMITS.items():
            if len(str(d.get(k) or "")) > limit:
                p.append(f"'{k}' is {len(str(d[k]))} characters; rewrite it shorter (max {limit})")
        if len(str(d.get("image_prompt") or "").split()) < 40:
            p.append("image_prompt is too short; write 70-140 words")
        claims = unsupported_claims([d.get(k) for k in ("headline_top", "headline_highlight", "subheadline")])
        if claims:
            p.append(f"remove unsupported claims {claims}: no guarantees, awards or invented numbers")
        return p
    return check


async def make_clean_brief(brand: Brand, idea: str, history: list[dict], feedback: dict | None = None,
                           industry: str | None = None, scene: str | None = None, service: str | None = None,
                           message: str = "") -> dict:
    """Brief for the clean style: a typed idea (followed exactly) or an industry demo."""
    if industry:
        user = f"INDUSTRY: {industry}\nSCENE for the photo: {scene}" + (f"\nCLIENT NOTE (follow it): {idea}" if idea else "")
    else:
        user = f"IDEA: {idea}"
        if service and brand.service(service):
            user += (f"\nSERVICE: {service}. Connect the idea to this {brand.name} service in the text and the picture, "
                     f"and use it as 'service'.")
    if message.strip():
        user += (f"\nTEXT ON THE PICTURE (written by the client, used word for word): \"{message.strip()}\". "
                 "Make the photo fit this message.")
    recent = [h["brief"] for h in history[:12] if h.get("brief")]
    if recent:
        user += "\n\nRECENT HEADLINES - write a different one:\n" + "\n".join(
            f"- {b.get('headline_top', '')} {b.get('headline_highlight', '')}" for b in recent)
    if feedback:
        user += (f"\n\nTHE PREVIOUS VERSION WAS REJECTED. Make a clearly different version (same idea).\n"
                 f"Reason given: {feedback.get('reason') or '(no reason given)'}")
    check = _check_clean(brand)
    data, used = await ask_json(_clean_system(brand, industry), user, check, temperature=0.85)
    svc = data.get("service")
    brief = {
        "template": "clean", "service": svc if svc and brand.service(svc) else "General",
        "angle": str(data.get("angle") or "").strip(), "benefits": [],
        **{k: _trim(data.get(k), limit) for k, limit in CLEAN_LIMITS.items()},
        "image_prompt": str(data.get("image_prompt") or "").strip(), "_model": used,
    }
    if service and brand.service(service) and not industry:
        brief["service"] = service
    if industry:
        brief.update(_eyebrow=industry, _contact="site", _industry=industry)
    else:
        brief["_contact"] = "full"
    return use_message(brief, message) if message.strip() else brief


# ---------- the client's own photos and videos ----------

UPLOAD_TEMPLATES = {"image": "photo", "carousel": "carousel", "video": "video"}


def _look_system(brand: Brand, kind: str) -> str:
    what = {"image": "a photo",
            "carousel": "the photos of one carousel post",
            "video": "frames from one video (from its beginning, middle and end)"}[kind]
    if kind == "carousel":
        seen = ("one short sentence per photo, in order (Photo 1: ..., Photo 2: ...): the subject, the people (what "
                "they do and wear), objects and place")
        how = ("\nThe photos are SEPARATE photos that people swipe through one by one. Several of them are shown "
               "together on numbered contact sheets only so you can see them all; never call them a collage or a grid.")
    elif kind == "video":
        seen = "2-4 sentences: what the video shows overall: the subject, the people (what they do and wear), objects, place and mood"
        how = ("\nThe pictures are SEPARATE frames taken at different moments of ONE video. Describe the video as a "
               "whole; never call it a split screen or a collage.")
    else:
        seen = "2-4 sentences: the subject, the people (what they do and wear), objects, place and mood"
        how = ""
    return f"""You look at {what} that {brand.name} ({brand.company['type']} in Dubai) wants to post on its social media.
Describe exactly what is visible, factually, so a copywriter can write the captions. Never guess brand names,
places or facts you cannot see.{how}

Reply with ONLY this JSON object:
{{"what_we_see": "{seen}",
  "visible_text": "readable words or logos in it, or empty",
  "service": "the exact name of the service it clearly shows, from this list, otherwise General: {'; '.join(brand.service_names)}",
  "angle": "one sentence: what a post about it could say for {brand.name}",
  "quality": "empty if it is fine, otherwise a short note (blurry, very dark, cut-off faces, not suitable for business)"}}"""


async def make_upload_brief(brand: Brand, media: dict, pictures: list[bytes], note: str = "",
                            service: str | None = None, spoken: str = "") -> dict:
    """What the AI sees in the client's photo(s) or video frames. Captions are then written from this."""
    from .imagegen import pool
    from .uploads import clock
    kind = media["kind"]
    user = f"CLIENT NOTE: {note}" if note else "CLIENT NOTE: (none)"
    if kind == "carousel":
        user += f"\nThe carousel has {len(media['items'])} photos, in this order."
    if kind == "video":
        v = media["video"]
        shape = "vertical" if v["height"] > v["width"] else "horizontal" if v["width"] > v["height"] else "square"
        user += f"\nVIDEO: {clock(v['duration'])} long, {shape}."
        if spoken:
            user += f"\nSPOKEN IN THE VIDEO (may be in another language): {spoken[:1200]}"
    system = _look_system(brand, kind)
    try:
        data, used = await ask_vision_json(system, user, pictures)
    except LLMError as first:
        try:  # backup: Gemini (the picture accounts) can look at pictures too
            text, slot = await pool.describe(f"{system}\n\n{user}", pictures)
            data, used = _extract_json(text), f"gemini/{slot}"
        except Exception as second:
            if not note:
                raise LLMError(f"The AI could not look at the {UPLOAD_TEMPLATES[kind]} right now (it is busy or "
                               f"down). Press Retry in a minute. Details: {str(first)[:160]} | {str(second)[:100]}") from None
            data, used = {"what_we_see": note, "angle": note}, "client note only (the AI could not look at it)"
    svc = str(data.get("service") or "")
    brief = {
        "template": UPLOAD_TEMPLATES[kind], "media": kind,
        "service": service if service and brand.service(service) else (svc if brand.service(svc) else "General"),
        "angle": str(data.get("angle") or "").strip()[:300],
        "what_we_see": str(data.get("what_we_see") or "").strip()[:900],
        "visible_text": str(data.get("visible_text") or "").strip()[:200],
        "headline_top": "", "headline_highlight": "", "subheadline": "", "benefits": [], "image_prompt": "",
        "_quality": str(data.get("quality") or "").strip()[:200], "_model": used,
        "_thumb": (media["poster"] if kind == "video" else media["items"][0])["thumb"],
    }
    if note:
        brief["client_note"] = note
    if spoken:
        brief["spoken_in_video"] = spoken[:1200]
    return brief


def image_prompt(brand: Brand, brief: dict) -> str:
    v = brand.profile["visual"]
    if brief["template"] == "clean":
        avoid = ", ".join(v["avoid_in_images"])
        return (
            f"Generate a photo. {brief['image_prompt'].strip()}\n\n"
            f"Style: {CLEAN_STYLE}.\nComposition: {CLEAN_COMPOSITION}.\nFormat: portrait 4:5, high resolution.\n"
            f"Strictly avoid: {avoid}. The image must contain absolutely NO text, letters, numbers, words, logos, "
            "labels, signs, flags or watermarks anywhere. Create the image now, do not ask questions."
        )
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


# Caption length limits per platform (characters); the AI is asked for less, see PLATFORM_RULES.
CAPTION_LIMITS = {"instagram": 2200, "linkedin": 3000, "threads": 480, "tiktok": 2200, "pinterest": 500,
                  "googlebusiness": 1500, "bluesky": 300, "mastodon": 500, "substack": 2000,
                  "tiktok_title": 90, "pinterest_title": 100}
EXTRA_FIELDS = {"threads": "threads_topic", "tiktok": "tiktok_title", "pinterest": "pinterest_title"}


def _platform_rules(brand: Brand) -> dict[str, str]:
    c = brand.company
    contact = f"📞 {c['phone']} · 🌐 {c['website']} · ✉️ {c['email']}"
    return {
        "facebook": f"""60-150 words. Strong first-line hook. 2-3 short paragraphs or a short ✅ list. End with the call to action
  using the exact contact details: {contact}. Last line: 4-7 hashtags. At most 3 emojis besides the contact icons.""",
        "instagram": f"""80-150 words, written for Instagram. A scroll-stopping first line (Instagram cuts it after about 125
  characters), then short lines with line breaks and up to 5 emojis. Links are not clickable on Instagram: write the
  contact as plain text, 📞 {c['phone']} · 🌐 {c['website']}. Last line: 10-15 relevant hashtags (service + Dubai/UAE tags).""",
        "linkedin": f"""120-220 words, professional and insightful, for business owners and IT/facility managers. Open with a
  business insight or a question, then the problem, the solution and the concrete benefits in 2-4 short paragraphs
  (a short ✅ list is fine). No slang, at most 2 emojis. End with the call to action using the exact contact details:
  {contact}. Last line: 3-5 hashtags.""",
        "x": f"""MAXIMUM 240 characters in total including hashtags and the website. One punchy line, the benefit,
  {c['website']}, and 1-2 hashtags. At most 1 emoji.""",
        "threads": "MAXIMUM 450 characters. Conversational, 2-4 short lines, ends with the call to action. No hashtags.",
        "tiktok": f"""60-120 words for a TikTok photo post: casual and energetic, a hook in the first line, short lines.
  Links are not clickable: write {c['website']} as text. Last line: 3-6 hashtags.""",
        "pinterest": f"""MAXIMUM 450 characters: 1-3 keyword-rich sentences saying what the service is and its benefit for
  businesses in Dubai/UAE, then {c['website']}. 2-4 hashtags.""",
        "googlebusiness": f"""60-120 words for a Google Business Profile update: a clear local message for businesses in
  Dubai and the UAE, the service and its benefits, ends by inviting them to visit {c['website']}. NO phone numbers
  (Google rejects posts that contain them), NO hashtags, at most 1 emoji.""",
        "bluesky": f"""MAXIMUM 280 characters in total: 1-2 short sentences with the benefit, {c['website']}, 1-2 hashtags.""",
        "mastodon": f"""MAXIMUM 450 characters: 2-3 short sentences, {c['website']}, 2-3 hashtags written in CamelCase
  (e.g. #SmartSecurity).""",
        "substack": f"""A short Substack Note, 50-120 words: a helpful tip or insight about the topic, then how
  {brand.name} helps and {c['website']}. No hashtags.""",
    }


def _copy_keys(platforms: list[str]) -> list[str]:
    return ["title", *platforms] + [EXTRA_FIELDS[p] for p in platforms if p in EXTRA_FIELDS]


def _copy_system(brand: Brand, service: str | None = None, platforms: list[str] | None = None,
                 mode: str = "brand", topic: str = "", industry: str | None = None, media: str | None = None) -> str:
    platforms = platforms or get_settings().post_platform_list
    if mode == "demo":
        return f"""You write ONE sample caption for a DEMO post: a typical premium {industry} business in Dubai (never a
real company or brand name). It shows a prospect what their social media could look like.
- facebook: 50-110 words, a hook in the first line, short paragraphs, warm and premium, ends with an invitation to
  book or visit. No phone numbers, emails or websites. Last line: 4-6 hashtags for {industry} in Dubai.
- title: short internal title, max 60 characters.
- Never invent prices, discounts, awards or numbers.

Reply with ONLY this JSON object:
{{"title": "...", "facebook": "..."}}"""
    tags = brand.profile["hashtags"]
    pool = "; ".join(f"{k}: {' '.join(v)}" for k, v in tags["by_category"].items())
    rules = _platform_rules(brand)
    if media == "video":  # Buffer: a TikTok video's text can be at most 150 characters
        rules["tiktok"] = "MAXIMUM 140 characters for a TikTok video: a short hook and 2-4 hashtags."
    lines = "\n".join(f"- {p}: {rules[p]}" for p in platforms)
    if "threads" in platforms:
        lines += "\n- threads_topic: ONE topic for Threads, 1-3 words, no '#', no '&' or '.', max 40 characters."
    if "tiktok" in platforms:
        lines += "\n- tiktok_title: a catchy title for the TikTok photo post, max 80 characters, no hashtags."
    if "pinterest" in platforms:
        lines += "\n- pinterest_title: a clear, searchable Pin title, max 90 characters, no hashtags."
    spec = ", ".join(f'"{k}": "..."' for k in _copy_keys(platforms))
    if mode == "idea":
        c = brand.company
        intro = (f"You are the social media copywriter for {brand.name} ({c['type']}). Write the captions for ONE post "
                 f"about exactly this topic, the client's own idea: {topic}\nStay on that topic; do not turn it into a "
                 f"different subject or a list of {brand.name} services.")
        kit = f"CONTACT DETAILS (use exactly): 📞 {c['phone']} · 🌐 {c['website']} · ✉️ {c['email']}"
    elif mode == "upload":
        thing = {"video": "video", "carousel": "carousel of photos"}.get(media, "photo")
        intro = (f"You are the social media copywriter for {brand.name} ({brand.company['type']}). The client is posting "
                 f"their OWN {thing}. Write the captions about exactly what it shows (what_we_see in the brief) and the "
                 f"client's note" + (", and what is said in it (spoken_in_video)" if media == "video" else "")
                 + ". Never describe people, places or things that are not in it, and never invent facts about it."
                 + " Use what_we_see to stay accurate, but write real marketing captions about the work and its "
                   "benefits for the customer: do not narrate or list what each photo or frame shows."
                 + (" On Instagram the video is published as a Reel." if media == "video" else "")
                 + (" People swipe through the photos one by one: never call them a collage or a grid."
                    if media == "carousel" else ""))
        kit = f"BRAND KIT\n{brand.context_text(all_services=False, focus=service)}"
    else:
        intro = f"You are the social media copywriter for {brand.name}. Write the captions for ONE post from a creative brief."
        kit = f"BRAND KIT\n{brand.context_text(all_services=False, focus=service)}"
    title = ("- title: a catchy public title for this post, max 60 characters, no hashtags (it is the video title on "
             "Facebook, the TikTok and Pinterest title, and the subject of the approval email)." if mode == "upload" else
             "- title: short internal title for the approval email, max 60 characters.")
    return f"""{intro}
Each platform gets its own caption, written in that platform's style (not the same text copied).

{kit}

PLATFORM RULES
{lines}
{title}
- Hashtags always include {' '.join(tags['always'])}; pick the rest from: {pool}.
- Contact details must be written exactly as above. Never write amanasoft.net.
- Only use facts from the brand kit and the brief. Never invent numbers, prices, discounts, client names or awards.

Reply with ONLY this JSON object:
{{{spec}}}"""


def _check_copy(brand: Brand, platforms: list[str] | None = None, limits: dict | None = None):
    phone_digits = re.sub(r"\D", "", brand.company["phone"])
    platforms = platforms or get_settings().post_platform_list
    limits = limits or CAPTION_LIMITS

    def check(d: dict) -> list[str]:
        p = []
        for k in ("title", *platforms):
            if not str(d.get(k) or "").strip():
                p.append(f"'{k}' is missing")
        if "x" in platforms:
            x_len = x_length(str(d.get("x") or ""))
            if x_len > 270:
                p.append(f"'x' is about {x_len} characters on X; it must be under 240. Make it shorter.")
        for k, limit in limits.items():
            if k in platforms and len(str(d.get(k) or "")) > limit:
                p.append(f"'{k}' is {len(str(d[k]))} characters; max {limit}. Make it shorter.")
        if "instagram" in platforms and len(re.findall(r"#\w+", str(d.get("instagram") or ""))) > 30:
            p.append("'instagram' has more than 30 hashtags; Instagram allows 30 at most (use 10-15)")
        for extra in (EXTRA_FIELDS[q] for q in platforms if q in EXTRA_FIELDS and EXTRA_FIELDS[q] != "threads_topic"):
            if not str(d.get(extra) or "").strip():
                p.append(f"'{extra}' is missing")
            elif len(str(d[extra])) > limits[extra]:
                p.append(f"'{extra}' is {len(str(d[extra]))} characters; max {limits[extra]}")
        if "googlebusiness" in platforms:
            g = str(d.get("googlebusiness") or "")
            if re.search(r"\+?\d[\d\s-]{7,}\d", g):
                p.append("'googlebusiness' must not contain a phone number (Google rejects such posts); remove it")
            if "#" in g:
                p.append("'googlebusiness' must not contain hashtags")
        all_text = " ".join(str(d.get(k) or "") for k in platforms)
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
                    feedback: dict | None = None, platforms: list[str] | None = None, mode: str = "brand",
                    industry: str | None = None, media: str | None = None) -> dict:
    public_brief = {k: v for k, v in brief.items() if not k.startswith("_") and k != "image_prompt"
                    and (v or mode != "upload")}  # an upload's brief has no on-image text: leave the empty fields out
    user = f"IDEA: {idea or '(none, use the brief)'}\n\nCREATIVE BRIEF: {json.dumps(public_brief, ensure_ascii=False)}"
    if plan.get("caption_style"):
        user += f"\n\nCAPTION STYLE for this post: {plan['caption_style']}."
    if brief.get("_message"):
        user += f"\nTEXT ON THE PICTURE (the client's own words): \"{brief['_message']}\". The captions build on it."
    openings = [o for o in recent_openings(history) if o]
    if openings:
        user += "\nDo NOT start the Facebook caption like any of these recent openings:\n- " + "\n- ".join(openings)
    tags = [" ".join(re.findall(r"#\w+", (h.get("captions") or {}).get("facebook", ""))) for h in history[:5]]
    if any(tags):
        user += "\nRecent hashtag sets (use a different mix): " + " | ".join(t for t in tags if t)
    if feedback and feedback.get("reason"):
        user += f"\n\nThe previous version was rejected. Reason: {feedback['reason']}. Write clearly different captions."

    platforms = ["facebook"] if mode == "demo" else list(dict.fromkeys(["facebook", *(platforms or get_settings().post_platform_list)]))
    limits = {**CAPTION_LIMITS, "tiktok": 150} if media == "video" else CAPTION_LIMITS
    base_check = _check_copy(brand, platforms, limits)
    data, used, problems = {}, "", []
    for round_no in range(3):
        extra = ("\n\nYOUR LAST TRY REPEATED EARLIER POSTS:\n- " + "\n- ".join(problems)) if problems else ""
        data, used = await ask_json(_copy_system(brand, brief.get("service"), platforms, mode, idea, industry, media),
                                    user + extra,
                                    lambda d: base_check(d) or captions_repeat(d, history),
                                    temperature=0.75 + 0.05 * round_no)
        problems = captions_repeat(data, history)
        if not problems:
            break
    out = {k: str(data.get(k) or "").strip() for k in _copy_keys(platforms)}
    if "x" in out:
        out["x"] = _fit_x(out["x"])
    for k, limit in limits.items():  # last resort only; the checks above ask the AI to shorten first
        if k in out and len(out[k]) > limit:
            out[k] = out[k][:limit - 10].rsplit(" ", 1)[0] + "…"
    if "threads_topic" in out:
        out["threads_topic"] = re.sub(r"[#&.]", "", out["threads_topic"]).strip()[:40]
    for k in ("tiktok_title", "pinterest_title"):
        if k in out:
            out[k] = re.sub(r"#\w+", "", out[k]).strip()[:CAPTION_LIMITS[k]]
    out["title"] = (out["title"][:80] or f"{brief['headline_top']} {brief['headline_highlight']}".strip()
                    or str(brief.get("angle") or "New post")[:60])
    out["_model"] = used
    if problems:
        out["_variety_warning"] = problems
    return out
