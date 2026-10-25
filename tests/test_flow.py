"""End-to-end test of the whole flow with fake AI, Gemini, email and Buffer (no keys needed).

Run:  .venv/bin/python -m pytest -q
"""
import asyncio
import base64
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path

os.environ.update({
    "DATA_DIR": tempfile.mkdtemp(prefix="post-agent-test-"), "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
    "APP_SECRET": "test-secret", "DASHBOARD_PASSWORD": "pw", "PUBLIC_BASE_URL": "http://testserver",
    "REVIEW_EMAIL_TO": "owner@example.com", "GMAIL_SENDER": "bot@example.com", "BUFFER_API_KEY": "x",
    "DRY_RUN_PUBLISH": "false", "PUBLISH_MODE": "shareNow", "POST_PLATFORMS": "facebook,instagram,linkedin,x,threads",
    "EMAIL_RELAY_URL": "https://script.google.com/macros/s/TEST/exec", "EMAIL_RELAY_SECRET": "s3",
    # never touch the real services that .env points to
    "EMAIL_MODE": "apps_script", "GMAIL_APP_PASSWORD": "", "BUFFER_CHANNEL_IDS": "", "GROQ_API_KEY": "",
    "OPENROUTER_API_KEY": "", "GEMINI_A_1PSID": "", "GEMINI_A_1PSIDTS": "", "GEMINI_B_1PSID": "", "GEMINI_B_1PSIDTS": "",
})

import httpx  # noqa: E402
import pytest  # noqa: E402
from PIL import Image  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import emailer, imagegen, publisher, writer  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sent: list[dict] = []
buffer_calls: list[dict] = []
image_mode = {"fail": False}

VOCAB = ("fast secure smart cloud cable camera office warehouse clinic school hotel shop network data backup "
         "team growth reliable modern simple local expert support monitor access door finger app web chat voice agent "
         "report branch mobile store sales leads speed safety storage server rack fiber copper switch router wifi "
         "desk lobby tower marina sunrise evening river desert garden bridge harbor station").split()
counter = {"n": 0}
prompts: list[tuple[str, str]] = []  # (system, user) of every AI call, for checking what the AI was told


def words(rnd, k):
    return " ".join(rnd.sample(VOCAB, k))


async def fake_ask_json(system, user, check=None, temperature=0.7):
    """Fake AI that writes different text every call, like a real one would."""
    import random
    counter["n"] += 1
    rnd = random.Random(counter["n"])
    prompts.append((system, user))
    if "ON-IMAGE TEXT (short and elegant)" in system:  # clean style: typed idea or industry demo
        data = {"service": "General", "angle": words(rnd, 8), "headline_top": words(rnd, 2).title(),
                "headline_highlight": words(rnd, 1), "subheadline": words(rnd, 5), "image_prompt": words(rnd, 50)}
        assert not (check(data) if check else []), check(data)
        return data, "fake/model"
    if "SAMPLE caption for a DEMO" in system:
        data = {"title": words(rnd, 4).capitalize(), "facebook": words(rnd, 30).capitalize() + ". #Dubai #Luxury"}
        assert not (check(data) if check else []), check(data)
        return data, "fake/model"
    if "creative director" in system:
        layout = re.search(r"LAYOUT: use '(\w+)'", user).group(1)
        data = {
            "post_type": "service_promo", "angle": words(rnd, 8),
            "service": "General" if "limo" in user.split("SERVICE:")[0].lower() else "Structured Cabling",
            "template": layout, "headline_top": words(rnd, 2).title(), "headline_highlight": words(rnd, 1).title(),
            "subheadline": words(rnd, 7), "body": words(rnd, 14),
            "benefits": [words(rnd, 2).title() for _ in range(3)],
            "bullets": [words(rnd, 4).capitalize() for _ in range(4)],
            "image_prompt": words(rnd, 45),
        }
    else:
        data = {
            "title": words(rnd, 5).capitalize(),
            "facebook": words(rnd, 30).capitalize() + ". 📞 +971 55 940 8284 · 🌐 amanasoft.ae #Amanasoft #Dubai #UAE",
            "instagram": words(rnd, 25).capitalize() + ".\n📞 +971 55 940 8284 · 🌐 amanasoft.ae\n#Amanasoft #Dubai #UAE #CCTV",
            "linkedin": words(rnd, 40).capitalize() + ". 📞 +971 55 940 8284 · 🌐 amanasoft.ae #Amanasoft #UAE",
            "x": words(rnd, 10).capitalize() + " amanasoft.ae #Amanasoft",
            "threads": words(rnd, 20).capitalize() + " amanasoft.ae",
            "threads_topic": "Structured Cabling",
        }
    assert not (check(data) if check else []), check(data)
    return data, "fake/model"


def fake_photo() -> bytes:
    """A different picture every call (random coloured blocks)."""
    import io
    import random
    from PIL import Image, ImageDraw
    rnd = random.Random(counter["n"] * 7919)
    img = Image.new("RGB", (512, 512), tuple(rnd.randint(0, 255) for _ in range(3)))
    d = ImageDraw.Draw(img)
    for _ in range(12):
        x, y = rnd.randint(0, 400), rnd.randint(0, 400)
        d.rectangle((x, y, x + rnd.randint(40, 200), y + rnd.randint(40, 200)), fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


async def fake_generate(prompt):
    assert "NO text" in prompt
    while image_mode.get("hold"):  # lets a test act while a post is being made
        await asyncio.sleep(0.05)
    if image_mode["fail"]:
        raise imagegen.ImageGenError("All Gemini accounts failed. A: cookie expired")
    return fake_photo(), "A"


async def fake_send(to, subject, html, text, inline=None):
    sent.append({"to": to, "subject": subject, "html": html, "inline": inline})


async def fake_gql(query, variables=None):
    if "organizations" in query:
        return {"account": {"organizations": [{"id": "org1", "name": "Amanasoft"}]}}
    if "channels" in query:
        return {"channels": [
            {"id": "fb1", "name": "Amanasoft", "service": "facebook", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
            {"id": "ig1", "name": "amanasoft", "service": "instagram", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
            {"id": "x1", "name": "@amanasoft", "service": "twitter", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
            {"id": "th1", "name": "amanasoft", "service": "threads", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
            {"id": "li1", "name": "Amanasoft", "service": "linkedin", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
        ]}
    buffer_calls.append(variables["input"])
    return {"createPost": {"post": {"id": f"bp-{len(buffer_calls)}", "dueAt": None}}}


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(writer, "ask_json", fake_ask_json)
    mp.setattr(imagegen.pool, "generate", fake_generate)
    mp.setattr(emailer, "send", fake_send)
    mp.setattr(publisher, "gql", fake_gql)
    with TestClient(app) as c:
        assert c.post("/login", data={"password": "pw"}, follow_redirects=False).status_code == 303
        yield c
    mp.undo()


def wait_for(client, post_id, statuses, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        p = client.get(f"/api/posts/{post_id}").json()
        if p["status"] in statuses:
            return p
        time.sleep(0.2)
    raise AssertionError(f"post stuck in {p['status']}: {p.get('error')}")


def links(html):
    """{"approve": url, "reject": url} from the review email."""
    found = re.findall(r'href="http://testserver(/review/[^"]+?action=(approve|reject|cancel)[^"]*)"', html)
    return {action: url.replace("&amp;", "&") for url, action in found}


def test_health(client):
    data = client.get("/health").json()
    assert data["ok"] is True and data["awake_since"] and data["version"] and "last_ok" in data["heartbeat"]
    assert client.head("/health").status_code == 200  # uptime checkers often use HEAD
    assert client.get("/health?from=supabase").json()["last_visit"]["supabase"]  # the Supabase keep-awake job is visible


def test_login_required():
    with TestClient(app) as anon:
        assert anon.get("/", follow_redirects=False).status_code == 303
        assert anon.get("/api/posts").status_code == 401


def test_idea_to_published(client):
    sent.clear()
    buffer_calls.clear()
    post = client.post("/api/posts", json={"idea": "Promote structured cabling for new offices"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    assert post["final_image_url"].startswith("http://testserver/media/")
    assert client.get(post["final_image_url"].replace("http://testserver", "")).status_code == 200

    mail = sent[-1]
    assert mail["subject"].startswith("Review post") and "post" in mail["inline"]
    urls = links(mail["html"])
    page = client.get(urls["approve"])
    assert page.status_code == 200 and "Publishing to" in page.text and 'id="go"' in page.text
    # opening the link alone (like an email virus scanner would) must NOT publish anything
    assert get(client, post["id"])["status"] == "pending_review" and not buffer_calls

    token = re.search(r"t=([0-9a-f]+)", urls["approve"]).group(1)
    done = client.post(f"/review/{post['id']}", data={"action": "approve", "t": token})  # what the page sends by itself
    assert done.status_code == 200 and "Published ✓" in done.text
    post = client.get(f"/api/posts/{post['id']}").json()
    assert post["status"] == "published"
    assert {c["service"] for c in post["publish_results"]["channels"]} == {"facebook", "instagram", "linkedin", "twitter", "threads"}
    assert {c["channelId"] for c in buffer_calls} == {"fb1", "ig1", "li1", "x1", "th1"}
    ig = next(c for c in buffer_calls if c["channelId"] == "ig1")
    assert ig["metadata"] == {"instagram": {"type": "post", "shouldShareToFeed": True}} and ig["text"] == post["captions"]["instagram"]
    assert next(c for c in buffer_calls if c["channelId"] == "li1")["text"] == post["captions"]["linkedin"]
    fb = next(c for c in buffer_calls if c["channelId"] == "fb1")
    assert fb["metadata"] == {"facebook": {"type": "post"}} and fb["assets"][0]["image"]["url"] == post["final_image_url"]
    assert next(c for c in buffer_calls if c["channelId"] == "x1")["text"] == post["captions"]["x"]
    assert sent[-1]["subject"].startswith("✅ Published to")

    # clicking Approve again must not publish twice
    again = client.post(f"/review/{post['id']}", data={"action": "approve", "t": token})
    assert "Already handled" in again.text and len(buffer_calls) == 5


def test_reject_makes_new_version(client):
    sent.clear()
    post = client.post("/api/posts", json={"idea": "CCTV for warehouses", "template": "spotlight"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    old = links(sent[-1]["html"])
    r = client.post(f"/api/posts/{post['id']}/reject", json={"reason": "Darker look please"})
    assert r.status_code == 200
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["attempt"] == 2 and len(post["history"]) == 1 and post["history"][0]["reason"] == "Darker look please"
    assert post["brief"]["template"] == "spotlight"
    assert sent[-1]["subject"].endswith("(version 2)")
    stale = client.get(old["approve"])
    assert stale.status_code == 403 and "not valid anymore" in stale.text  # links from the old email stop working
    assert "<html" in stale.text  # shown as a readable page, not raw JSON


def test_failure_alert_and_retry(client):
    sent.clear()
    image_mode["fail"] = True
    post = client.post("/api/posts", json={"idea": "Cloud migration for SMEs"}).json()
    post = wait_for(client, post["id"], ["failed"])
    assert "Gemini" in post["error"] and post["captions"]  # text was kept
    assert any(m["subject"].startswith("❌ Post failed") for m in sent)
    image_mode["fail"] = False
    assert client.post(f"/api/posts/{post['id']}/retry").status_code == 200
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review"


def test_pages_render(client):
    post_id = client.get("/api/posts").json()[0]["id"]
    for url in ("/", f"/posts/{post_id}", "/schedule"):
        r = client.get(url)
        assert r.status_code == 200, url
    for gone in ("/settings", "/brand", "/api/settings/test/llm"):
        assert client.get(gone).status_code in (404, 405), gone  # removed from the website


def test_x_length_guard():
    long = "Word " * 80 + "amanasoft.ae #Amanasoft #Dubai #UAE"
    assert writer.x_length(writer._fit_x(long)) <= 280


def test_apps_script_relay(monkeypatch):
    """Apps Script answers a POST with a redirect to a one-time URL; the result is behind that URL."""
    seen = {}
    replies = iter([{"ok": True, "remaining_today": 99}, {"ok": False, "error": "wrong secret"}, None])

    def handler(request):
        if request.url.host == "script.google.com":
            seen["payload"] = json.loads(request.content)
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo?k=1"})
        seen["followed"] = request.method
        reply = next(replies)
        return httpx.Response(200, json=reply) if reply else httpx.Response(200, text="<html>Sign in</html>")

    real = httpx.AsyncClient
    monkeypatch.setattr(emailer.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    send = emailer._send_apps_script
    asyncio.run(send(["me@example.com"], "Subj", '<img src="cid:post">', "text", {"post": b"\xff\xd8jpeg"}))
    assert seen["followed"] == "GET"
    assert seen["payload"]["secret"] == "s3" and seen["payload"]["to"] == "me@example.com"
    assert base64.b64decode(seen["payload"]["inline"]["post"]) == b"\xff\xd8jpeg"
    with pytest.raises(emailer.EmailError, match="wrong secret"):
        asyncio.run(send(["me@example.com"], "S", "h", "t", None))
    with pytest.raises(emailer.EmailError, match="Anyone"):
        asyncio.run(send(["me@example.com"], "S", "h", "t", None))


def test_auto_topic_needs_no_idea(client):
    post = client.post("/api/posts", json={"idea": "", "service": None, "post_type": None}).json()
    assert post["idea"].startswith("Auto: ")
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    plan = post["brief"]["_plan"]
    assert plan["service_source"] == "auto" and plan["person"] and plan["caption_style"]


def test_chosen_service_and_type_are_kept(client):
    post = client.post("/api/posts", json={"service": "IP CCTV & AHD CCTV Systems", "post_type": "tip"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    plan = post["prefs"]["plan"]
    assert plan["service_source"] == "user" and plan["post_type"] == "tip"


def test_planner_never_repeats_back_to_back():
    import random
    from app.brand import load_brand
    from app.variety import PEOPLE, SETTINGS, make_plan
    brand, history, plans = load_brand(), [], []
    rnd = random.Random(1)
    for _ in range(24):
        plan = make_plan(history, None, None, None, brand, rnd)
        plans.append(plan)
        history.insert(0, {"brief": {"_plan": plan}})
    for a, b in zip(plans, plans[1:]):
        for key in ("template", "person", "setting", "shot", "light", "caption_style", "service", "post_type", "industry"):
            assert a[key] != b[key] or key == "light" and a["template"] == b["template"] == "skyline", key
    assert {p["person"] for p in plans[:len(PEOPLE)]} == set(PEOPLE)  # every person used before any comes back
    assert {p["setting"] for p in plans if p["template"] != "skyline"} <= set(SETTINGS)
    services = [p["service"] for p in plans[:18]]
    assert len(set(services)) == 18  # all 18 services before any repeats


def test_repeat_check_catches_copies():
    from app.variety import brief_repeats, captions_repeat
    old = {"brief": {"headline_top": "Protect your business from", "headline_highlight": "Cyber Threats",
                     "subheadline": "Security for your data, systems and network",
                     "benefits": ["Threat protection", "Secure data", "Business continuity"],
                     "image_prompt": "a security operations center at night with analysts in front of large monitors"},
           "captions": {"facebook": "Hackers never sleep. Neither does our team watching your network for threats.\nCall us.",
                        "x": "Hackers never sleep. amanasoft.ae", "threads": "Hackers never sleep."}}
    same = dict(old["brief"], headline_top="Protect your business from", headline_highlight="Cyber Threat")
    assert brief_repeats(same, [old])
    fresh = {"headline_top": "Never miss", "headline_highlight": "A Call", "subheadline": "AI receptionist 24/7",
             "benefits": ["Answers 24/7", "Books meetings", "More leads"],
             "image_prompt": "a sleek headset glowing on a reception desk with the marina behind"}
    assert brief_repeats(fresh, [old]) == []
    assert captions_repeat({"facebook": "Hackers never sleep! Neither does our team watching your network.",
                            "x": "x", "threads": "t"}, [old])
    assert captions_repeat({"facebook": "Missed calls are missed sales.", "x": "a", "threads": "b"}, [old]) == []


def test_picture_fingerprint():
    from app.variety import fingerprint, looks_like_earlier
    a, b = fake_photo(), None
    counter["n"] += 1
    b = fake_photo()
    fa, fb = fingerprint(a), fingerprint(b)
    assert looks_like_earlier(fa, [{"brief": {"_fingerprint": fa}}])
    assert not looks_like_earlier(fb, [{"brief": {"_fingerprint": fa}}])


# ---------------- scheduling ----------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from app import pipeline, scheduler  # noqa: E402
from app.timeutil import TZ, iso, local, now_utc, parse, to_local_input  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def get(client, post_id):
    return client.get(f"/api/posts/{post_id}").json()


def test_slots_are_dubai_times_and_days():
    sched = scheduler.validate({"enabled": True, "times": ["9:00", "19:30"], "days": [0, 1, 2, 3, 4]})
    assert sched["times"] == ["09:00", "19:30"]
    monday = datetime(2026, 10, 5, tzinfo=TZ)  # a Monday
    slots = scheduler.slots_between(sched, parse(monday), parse(monday + timedelta(days=7)))
    assert len(slots) == 10  # Mon-Fri, 2 a day; no weekend
    assert iso(slots[0]) == "2026-10-05T05:00:00+00:00"  # 09:00 Dubai = 05:00 UTC
    assert local(slots[1]).strftime("%a %H:%M") == "Mon 19:30"
    assert all(local(s).weekday() < 5 for s in slots)


def test_schedule_validation():
    for bad in ({"enabled": True, "times": []}, {"enabled": True, "times": ["25:00"]},
                {"enabled": True, "times": ["09:00"], "days": []}, {"times": ["09:00"], "prepare_minutes": 7}):
        with pytest.raises(scheduler.ScheduleError):
            scheduler.validate(bad)
    ok = scheduler.validate({"enabled": False, "times": [], "approval": "weird", "if_no_answer": "x"})
    assert ok["approval"] == "ask" and ok["if_no_answer"] == "wait"


def test_post_with_a_time_waits_for_it(client):
    buffer_calls.clear()
    sent.clear()
    at = to_local_input(now_utc() + timedelta(hours=2))
    post = client.post("/api/posts", json={"idea": "Cloud backup for clinics", "publish_at": at}).json()
    assert post["scheduled_for"]
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review"
    assert sent[-1]["subject"].startswith("Approve for")
    assert "cancel" in links(sent[-1]["html"])
    post = client.post(f"/api/posts/{post['id']}/approve").json()
    assert post["status"] == "scheduled"  # approved, but not published yet
    run(pipeline.publish_due())  # its time has not come
    assert get(client, post["id"])["status"] == "scheduled" and not buffer_calls
    run(pipeline.publish_due(parse(post["scheduled_for"]) + timedelta(minutes=1)))
    post = get(client, post["id"])
    assert post["status"] == "published"
    assert {c["mode"] for c in buffer_calls} == {"shareNow"}


def test_past_times_are_refused(client):
    r = client.post("/api/posts", json={"idea": "Late post", "publish_at": to_local_input(now_utc() - timedelta(hours=1))})
    assert r.status_code == 400


def test_daily_schedule_fully_automatic(client):
    sent.clear()
    buffer_calls.clear()
    slot_local = local(now_utc() + timedelta(minutes=30))
    hhmm = slot_local.strftime("%H:%M")
    r = client.post("/api/schedule", json={"enabled": True, "times": [hhmm], "days": list(range(7)),
                                           "approval": "auto", "prepare_minutes": 60, "if_no_answer": "wait"})
    assert r.status_code == 200, r.text
    made = [p for p in client.get("/api/posts").json() if (p.get("prefs") or {}).get("source") == "schedule"]
    assert len(made) == 1
    post = made[0]
    assert post["idea"].startswith("Auto: ")
    assert local(parse(post["scheduled_for"])).strftime("%H:%M") == hhmm
    # saving again (or the minute timer) must not make a second post for the same time
    client.post("/api/schedule", json=r.json())
    assert len([p for p in client.get("/api/posts").json() if p.get("scheduled_for") == post["scheduled_for"]]) == 1
    post = wait_for(client, post["id"], ["scheduled", "failed"])
    assert post["status"] == "scheduled" and post["prefs"]["auto_publish"] is True
    heads_up = [m for m in sent if m["subject"].startswith("Auto-post")]
    assert heads_up and "approve" not in links(heads_up[-1]["html"]) and "cancel" in links(heads_up[-1]["html"])
    run(pipeline.publish_due(parse(post["scheduled_for"]) + timedelta(seconds=30)))
    assert get(client, post["id"])["status"] == "published"
    page = client.get("/schedule")
    assert page.status_code == 200 and "Today" in page.text or "Tomorrow" in page.text
    client.post("/api/schedule", json={**r.json(), "enabled": False})


def test_publish_anyway_if_not_approved(client):
    """Daily schedule, 'Ask me first' + 'Publish it anyway': no answer by the time → it goes out."""
    buffer_calls.clear()
    hhmm = local(now_utc() + timedelta(minutes=40)).strftime("%H:%M")
    sched = {"enabled": True, "times": [hhmm], "days": list(range(7)), "approval": "ask",
             "prepare_minutes": 60, "if_no_answer": "publish"}
    assert client.post("/api/schedule", json=sched).status_code == 200
    post = next(p for p in client.get("/api/posts").json()
                if (p.get("prefs") or {}).get("source") == "schedule" and
                local(parse(p["scheduled_for"])).strftime("%H:%M") == hhmm and p["status"] != "published")
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["prefs"]["if_no_answer"] == "publish" and post["prefs"]["auto_publish"] is False
    run(pipeline.publish_due(parse(post["scheduled_for"]) - timedelta(seconds=10)))
    assert get(client, post["id"])["status"] == "pending_review"  # not yet
    run(pipeline.publish_due(parse(post["scheduled_for"]) + timedelta(seconds=10)))
    assert get(client, post["id"])["status"] == "published"
    client.post("/api/schedule", json={**sched, "enabled": False})


def test_missed_reschedule_cancel(client):
    sent.clear()
    at = to_local_input(now_utc() + timedelta(hours=1))
    post = client.post("/api/posts", json={"idea": "Access control for schools", "publish_at": at}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    client.post(f"/api/posts/{post['id']}/approve")
    run(pipeline.publish_due(parse(post["scheduled_for"]) + timedelta(hours=4)))  # server was "offline" for hours
    post = get(client, post["id"])
    assert post["status"] == "missed"
    assert any(m["subject"].startswith("⏰") for m in sent)
    new_time = to_local_input(now_utc() + timedelta(hours=5))
    post = client.post(f"/api/posts/{post['id']}/reschedule", json={"publish_at": new_time}).json()
    assert post["status"] == "scheduled" and to_local_input(post["scheduled_for"]) == new_time
    assert client.post(f"/api/posts/{post['id']}/reschedule",
                       json={"publish_at": to_local_input(now_utc() - timedelta(hours=1))}).status_code == 400
    post = client.post(f"/api/posts/{post['id']}/cancel").json()
    assert post["status"] == "cancelled"
    run(pipeline.publish_due(now_utc() + timedelta(hours=6)))
    assert get(client, post["id"])["status"] == "cancelled"
    assert client.post(f"/api/posts/{post['id']}/publish-now").status_code == 409


def test_cancel_while_being_made(client):
    image_mode["hold"] = True
    try:
        post = client.post("/api/posts", json={"idea": "Cyber security for banks"}).json()
        wait_for(client, post["id"], ["imaging"])
        assert client.post(f"/api/posts/{post['id']}/cancel").json()["status"] == "cancelled"
    finally:
        image_mode["hold"] = False
    time.sleep(1)
    post = get(client, post["id"])
    assert post["status"] == "cancelled" and not post.get("final_image_url")


def test_email_cancel_link(client):
    sent.clear()
    at = to_local_input(now_utc() + timedelta(hours=3))
    post = client.post("/api/posts", json={"idea": "Mobile app for restaurants", "publish_at": at}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    url = links(sent[-1]["html"])["cancel"]
    page = client.get(url)
    assert page.status_code == 200 and "Cancelling this post" in page.text
    assert get(client, post["id"])["status"] == "pending_review"  # opening alone changes nothing
    token = re.search(r"t=([0-9a-f]+)", url).group(1)
    done = client.post(f"/review/{post['id']}", data={"action": "cancel", "t": token})
    assert "Cancelled" in done.text and get(client, post["id"])["status"] == "cancelled"


def test_publish_now_skips_the_wait(client):
    buffer_calls.clear()
    at = to_local_input(now_utc() + timedelta(hours=4))
    post = client.post("/api/posts", json={"idea": "Web chatbot for real estate", "publish_at": at}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    post = client.post(f"/api/posts/{post['id']}/approve").json()
    assert post["status"] == "scheduled"
    post = client.post(f"/api/posts/{post['id']}/publish-now").json()
    assert post["status"] == "published" and buffer_calls


def test_every_page_renders(client):
    ids = [p["id"] for p in client.get("/api/posts").json()]
    for url in ["/", "/schedule"] + [f"/posts/{i}" for i in ids]:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Traceback" not in r.text


def test_time_already_passed_today_is_not_posted(client):
    """Adding a time that has already passed today must not post anything now."""
    past = local(now_utc() - timedelta(minutes=30)).strftime("%H:%M")
    before = len(client.get("/api/posts?limit=500").json())
    r = client.post("/api/schedule", json={"enabled": True, "times": [past], "days": list(range(7)),
                                           "approval": "auto", "prepare_minutes": 60, "if_no_answer": "wait"})
    assert r.status_code == 200 and r.json()["saved_at"]
    assert len(client.get("/api/posts?limit=500").json()) == before
    client.post("/api/schedule", json={**r.json(), "enabled": False})


def test_server_downtime_slot_is_still_made_late(monkeypatch):
    """A time planned before the server went offline is still prepared late (up to 1 hour); older ones are skipped."""
    made = []

    async def fake_create(idea, template, **kw):
        made.append(kw["scheduled_for"])
        return {"id": f"fake-{len(made)}"}

    alerted = []

    async def fake_alert(slots):
        alerted.append(list(slots))

    async def go():
        from app.store import get_store
        monkeypatch.setattr(scheduler, "_lock", asyncio.Lock())  # a lock for this test's event loop
        monkeypatch.setattr(scheduler.pipeline, "create", fake_create)
        monkeypatch.setattr(scheduler.emailer, "alert_slots_missed", fake_alert)
        now = now_utc().replace(second=0, microsecond=0)
        recent, old = now - timedelta(minutes=20), now - timedelta(minutes=90)
        await get_store().set_setting("schedule_slots_done", [])
        await get_store().set_setting("schedule", {
            "enabled": True, "times": sorted({local(recent).strftime("%H:%M"), local(old).strftime("%H:%M")}),
            "days": list(range(7)), "approval": "ask", "prepare_minutes": 60, "if_no_answer": "wait",
            "saved_at": iso(now - timedelta(days=1))})
        await scheduler.prepare_due(now)
        await scheduler.prepare_due(now)  # a second tick must not make it again
        await get_store().set_setting("schedule", dict(scheduler.DEFAULT))
        return recent, old

    recent, old = run(go())
    assert made == [recent]
    assert alerted == [[old]]  # the skipped time is reported by email, once


def test_bad_schedule_input_gets_clear_400(client):
    for body in ({"enabled": True, "times": ["09:00"], "prepare_minutes": "abc"}, [1, 2],
                 {"enabled": True, "times": ["09:00"], "days": "all"}, {"enabled": True, "times": ["9am"]}):
        r = client.post("/api/schedule", json=body)
        assert r.status_code == 400, (body, r.status_code, r.text)
    r = client.post("/api/schedule", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    # a single time sent as text instead of a list is accepted (lenient), then switched off again
    r = client.post("/api/schedule", json={"enabled": False, "times": "09:00"})
    assert r.status_code == 200 and r.json()["times"] == ["09:00"]


def test_groq_rate_limit_is_waited_out_and_missing_model_skipped(monkeypatch):
    from app import llm
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "gone-model":
            return httpx.Response(404, json={"error": {"message": "The model `gone-model` does not exist", "code": "model_not_found"}})
        if calls.count("busy-model") == 1:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached. Please try again in 0.2s."}})
        assert body["reasoning_effort"] == "low" if body["model"].startswith("openai/gpt-oss") else True
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm, "_candidates", lambda: [c for c in [("groq", "k", "gone-model"), ("groq", "k", "busy-model")]
                                                    if f"groq/{c[2]}" not in llm._missing_models])
    data, used = run(llm.ask_json("Reply JSON", "{}"))
    assert data == {"ok": True} and used == "groq/busy-model"
    assert calls == ["gone-model", "busy-model", "busy-model"]  # 404 skipped, 429 waited out and retried
    assert "groq/gone-model" in llm._missing_models
    llm._missing_models.discard("groq/gone-model")


def test_groq_wait_time_parsing():
    from app.llm import _wait_seconds
    def resp(text, headers=None):
        return httpx.Response(429, text=text, headers=headers or {})
    assert abs(_wait_seconds(resp("Please try again in 1.8675s.")) - 1.8675) < 1e-6
    assert _wait_seconds(resp("try again in 7m12.5s")) == 432.5
    assert abs(_wait_seconds(resp("try again in 520ms")) - 0.52) < 1e-6
    assert _wait_seconds(resp("try again in 2m")) == 120
    assert _wait_seconds(resp("busy", {"retry-after": "8"})) == 8
    assert _wait_seconds(resp("no hint")) is None


def test_unsupported_claims_are_sent_back():
    from app.brand import load_brand
    from app.writer import _check_brief, _check_copy, unsupported_claims
    assert unsupported_claims(["Zero downtime", "20+ years of experience", "award-winning"]) == ["Zero downtime", "20+ years", "award"]
    assert unsupported_claims(["24/7 monitoring", "Cat6A", "ISO/IEC standards"]) == []
    brief = {"template": "wave", "headline_top": "Fast", "headline_highlight": "Networks", "subheadline": "Guaranteed speed",
             "benefits": ["Zero downtime", "Easy", "Secure"], "image_prompt": " ".join(["word"] * 40), "service": "General"}
    assert any("unsupported claims" in p for p in _check_brief(load_brand())(brief))
    copy = {"title": "t", "facebook": "100% uptime for your office", "x": "x", "threads": "t"}
    assert any("unsupported claims" in p for p in _check_copy(load_brand())(copy))


def test_old_email_link_explains_what_happened(client):
    """Your exact case: the post was approved elsewhere and published; then the email's Reject link is opened."""
    sent.clear()
    post = client.post("/api/posts", json={"idea": "Time attendance for retail branches"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    reject_url = links(sent[-1]["html"])["reject"]
    client.post(f"/api/posts/{post['id']}/approve")  # approved from the dashboard → published
    import html as _html
    page = _html.unescape(client.get(reject_url).text)
    assert "Already handled" in page and "already published" in page and "can't be rejected" in page
    token = re.search(r"t=([0-9a-f]+)", reject_url).group(1)
    done = client.post(f"/review/{post['id']}", data={"action": "reject", "t": token, "reason": "x"})
    assert "already published" in _html.unescape(done.text) and get(client, post["id"])["status"] == "published"
    events = run(pipeline.events(post["id"]))
    whats = [e["what"] for e in events]
    assert any(w.startswith("Created from the idea") for w in whats)
    assert any("review email sent" in w for w in whats)
    assert any(w.startswith("Approved") and e["by"] == "dashboard" for w, e in zip(whats, events))
    assert any(w.startswith("Published to") for w in whats)
    assert any("link opened, but the post was already published" in w and e["by"] == "email link" for w, e in zip(whats, events))
    assert "Activity" in client.get(f"/posts/{post['id']}").text


def test_one_tap_reject_with_quick_reason(client):
    sent.clear()
    post = client.post("/api/posts", json={"idea": "Biometric attendance for factories"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    html = sent[-1]["html"]
    quick = re.search(r'href="http://testserver(/review/[^"]+action=reject[^"]*?&amp;r=image)"', html)
    assert quick, "email has one-tap reject reasons"
    url = quick.group(1).replace("&amp;", "&")
    page = client.get(url)
    assert 'name="reason" value="Different image"' in page.text and "Making a new version" in page.text
    token = re.search(r"t=([0-9a-f]+)", url).group(1)
    done = client.post(f"/review/{post['id']}", data={"action": "reject", "t": token, "reason": "Different image"})
    assert "Making a new version" in done.text and "Different image" in done.text
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["attempt"] == 2 and post["history"][-1]["reason"] == "Different image"
    assert sent[-1]["subject"].endswith("(version 2)")


def test_new_cookie_in_env_wins_over_saved_copy(monkeypatch):
    """No Settings page: a fresh cookie put in .env / Render must replace the app's saved copy."""
    from app import imagegen
    from app.config import get_settings
    from app.store import get_store

    async def go():
        st, s = get_store(), get_settings()
        monkeypatch.setattr(s, "gemini_b_1psid", "PSID-ONE")
        monkeypatch.setattr(s, "gemini_b_1psidts", "TS-ONE")
        await st.set_setting("gemini_cookie_B", None)
        assert await imagegen.pool.cookies("B") == ("PSID-ONE", "TS-ONE")
        # Gemini refreshed it while running: the newest copy is used after a restart
        await st.set_setting("gemini_cookie_B", {**await st.get_setting("gemini_cookie_B"), "psidts": "TS-REFRESHED"})
        assert await imagegen.pool.cookies("B") == ("PSID-ONE", "TS-REFRESHED")
        # same login, but you pasted a fresh PSIDTS into .env → yours wins
        monkeypatch.setattr(s, "gemini_b_1psidts", "TS-PASTED")
        assert await imagegen.pool.cookies("B") == ("PSID-ONE", "TS-PASTED")
        # a different login in .env → it wins
        monkeypatch.setattr(s, "gemini_b_1psid", "PSID-TWO")
        monkeypatch.setattr(s, "gemini_b_1psidts", "TS-TWO")
        assert await imagegen.pool.cookies("B") == ("PSID-TWO", "TS-TWO")
        await st.set_setting("gemini_cookie_B", None)
        monkeypatch.setattr(s, "gemini_b_1psid", "")
        monkeypatch.setattr(s, "gemini_b_1psidts", "")
    run(go())


def test_failed_review_email_keeps_the_post_for_the_dashboard(client, monkeypatch):
    """If the email can't be sent (e.g. relay link not set), the post still waits for approval on the dashboard."""
    async def broken_send(*a, **kw):
        raise emailer.EmailError("EMAIL_RELAY_URL is not set yet")
    monkeypatch.setattr(emailer, "send", broken_send)
    post = client.post("/api/posts", json={"idea": "Network audit for hotels"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review"
    assert "could not be sent" in post["error"] and "EMAIL_RELAY_URL" in post["error"]
    assert post["brief"]["_thumb"].endswith("-thumb.jpg")
    thumb = client.get(post["brief"]["_thumb"].replace("http://testserver", ""))
    full = client.get(post["final_image_url"].replace("http://testserver", ""))
    assert thumb.status_code == 200 and len(thumb.content) < len(full.content) / 3
    assert client.post(f"/api/posts/{post['id']}/approve").json()["status"] == "published"


def test_heartbeat_visits_its_own_health_page(monkeypatch):
    from app import main
    visits = []

    def handler(request):
        visits.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    real = httpx.AsyncClient
    monkeypatch.setattr(main.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(main.settings, "public_base_url", "https://example.onrender.com")
    monkeypatch.setattr(main.settings, "heartbeat_minutes", 0.001)  # ~0.06 s for the test

    async def go():
        task = asyncio.create_task(main.heartbeat_loop())
        await asyncio.sleep(0.3)
        task.cancel()
    run(go())
    assert visits and all(v == "https://example.onrender.com/health?from=heartbeat" for v in visits)
    assert main.HEARTBEAT["on"] and main.HEARTBEAT["last_ok"]  # shown on /health, so it can be checked from outside

    visits.clear()  # on your computer (http://localhost) it stays off
    monkeypatch.setattr(main.settings, "public_base_url", "http://localhost:8000")
    run(main.heartbeat_loop())
    assert visits == []


def test_every_layout_renders_at_its_size():
    from app.brand import load_brand
    from app.composer import TEMPLATES, compose
    buf = io.BytesIO()
    Image.new("RGB", (1024, 1024), (40, 90, 160)).save(buf, "PNG")
    content = {"headline_top": "Keep every branch office online", "headline_highlight": "Without Interruption",
               "subheadline": "End-to-end managed networks, firewalls and Wi-Fi for growing companies across the UAE.",
               "body": "Smart, scalable networks for UAE businesses.", "benefits": ["Instant notifications",
               "Lower running costs", "Certified engineers"], "bullets": ["Live check-in", "Instant reports", "One dashboard"]}
    assert set(TEMPLATES) == {"wave", "spotlight", "skyline", "split", "cards", "frame",  # circle + checklist removed
                              "glass", "editorial", "arch", "band", "corporate", "neon"}  # the modern layouts
    square = ("wave", "spotlight", "split", "frame")
    for t in (*TEMPLATES, "clean"):
        img = Image.open(io.BytesIO(compose(t, buf.getvalue(), {"template": t, "service": "Network Solutions", **content},
                                            load_brand())))
        assert img.size == ((1080, 1080) if t in square else (1080, 1350)), t


def test_people_use_a_helmet_technician_instead_of_the_african_engineer():
    from app.variety import PEOPLE
    assert not any("african" in p.lower() for p in PEOPLE)
    assert any("safety helmet" in p for p in PEOPLE)


def test_mistyped_post_link_is_not_found_not_a_database_error():
    from app.store import SupabaseStore

    async def go():
        store = SupabaseStore("https://example.supabase.co", "sb_secret_test", "posts")

        async def no_network(*a, **kw):
            raise AssertionError("must not ask the database for a malformed id")
        store._rest = no_network
        return await store.get_post("8cedd695"), await store.claim("8cedd695", ["pending_review"], "scheduled")
    assert run(go()) == (None, None)


def test_text_is_never_cut_mid_thought():
    from app.writer import _trim
    cases = {
        "Amanasoft migrates, hosts and connects, so Dubai developers can focus on building great products":
            "Amanasoft migrates, hosts and connects",
        "Custom, secure sites built by certified engineers – from design to ongoing support for every campus":
            "Custom, secure sites built by certified engineers",
        "Custom iOS & Android apps for Dubai logistics firms to track inventory, drivers and deliveries live":
            "Custom iOS & Android apps for Dubai logistics firms to track inventory",
        "Smart networks built and supported for growing businesses with offices in every emirate of the UAE":
            "Smart networks built and supported for growing businesses with offices",
    }
    for text, expected in cases.items():
        assert _trim(text, 80) == expected, _trim(text, 80)
    assert _trim("Short and fine", 80) == "Short and fine"


def test_three_cookies_are_tried_in_order_and_every_dead_one_is_emailed(monkeypatch):
    """A fails -> B is tried; B fails -> C makes the picture. An alert goes out for A and for B."""
    from app import imagegen
    from app.store import get_store
    from gemini_webapi.exceptions import AuthError

    tried, alerts = [], []

    async def go():
        pool = imagegen.GeminiPool()

        async def fake_cookies(slot):
            return f"psid-{slot}", "ts"

        async def fake_generate(slot, prompt):
            tried.append(slot)
            if slot in ("A", "B"):
                raise AuthError(f"cookie {slot} expired")
            return b"picture"

        async def fake_alert(slot, error, others=None):
            alerts.append((slot, others))
        monkeypatch.setattr(pool, "cookies", fake_cookies)
        monkeypatch.setattr(pool, "_generate_with", fake_generate)
        monkeypatch.setattr(imagegen.emailer, "alert_cookie_dead", fake_alert)
        store = get_store()
        for slot in imagegen.SLOTS:
            await store.set_setting(f"gemini_status_{slot}", {"state": "ok"})
        result = await pool.generate("a photo")
        for slot in imagegen.SLOTS:
            await store.set_setting(f"gemini_status_{slot}", None)
        return result

    assert imagegen.SLOTS == ("A", "B", "C")
    assert run(go()) == (b"picture", "C")
    assert tried == ["A", "B", "C"]
    assert [a[0] for a in alerts] == ["A", "B"]
    assert alerts[1][1] == ["A: dead", "C: ok"]  # the email says which accounts still work


def test_client_platforms_facebook_instagram_linkedin():
    """The client's setup: a caption written for each of Facebook, Instagram and LinkedIn, each sent to its own channel."""
    from app.brand import load_brand
    from app.config import Settings
    from app.publisher import _input
    from app.writer import _check_copy, _copy_keys, _copy_system
    assert Settings(post_platforms="facebook,instagram,linkedin").post_platform_list == ["facebook", "instagram", "linkedin"]
    assert Settings(post_platforms="linkedin").post_platform_list == ["facebook", "linkedin"]  # Facebook text is always made
    platforms = ["facebook", "instagram", "linkedin"]
    assert _copy_keys(platforms) == ["title", "facebook", "instagram", "linkedin"]
    prompt = _copy_system(load_brand(), None, platforms)
    assert '"instagram": "..."' in prompt and '"linkedin": "..."' in prompt and '"x": "..."' not in prompt
    check = _check_copy(load_brand(), platforms)
    good = {"title": "t", "facebook": "Fast networks. 📞 +971 55 940 8284", "instagram": "Fast networks #Amanasoft",
            "linkedin": "Reliable networks for UAE offices. 📞 +971 55 940 8284"}
    assert check(good) == []
    assert any("'linkedin' is missing" in p for p in check({**good, "linkedin": ""}))
    assert any("30 hashtags" in p for p in check({**good, "instagram": " ".join(f"#tag{i}" for i in range(31))}))
    caps = {"facebook": "FB text", "instagram": "IG text", "linkedin": "LI text"}
    ch = lambda service: {"id": service, "service": service}  # noqa: E731
    assert _input(ch("instagram"), caps, "https://img")["metadata"] == {"instagram": {"type": "post", "shouldShareToFeed": True}}
    assert _input(ch("instagram"), caps, "https://img")["text"] == "IG text"
    assert _input(ch("linkedin"), caps, "https://img")["text"] == "LI text" and "metadata" not in _input(ch("linkedin"), caps, "https://img")
    assert _input(ch("twitter"), caps, "https://img")["text"] == "FB text"  # no X caption: the Facebook text is used


def test_every_buffer_channel_type_gets_its_own_post(monkeypatch):
    """Whatever the client connects in Buffer is detected; each type gets the right caption and settings."""
    from app import publisher
    from app.brand import load_brand
    from app.writer import _check_copy, _copy_keys

    def chan(i, service, **extra):
        return {"id": i, "name": f"{service} page", "service": service, "isQueuePaused": False, "isDisconnected": False,
                "isLocked": False, **extra}
    channels = [chan("fb", "facebook"), chan("tt", "tiktok"), chan("pin", "pinterest", metadata={"boards": [{"serviceId": "b1", "name": "Ideas"}]}),
                chan("gb", "googlebusiness"), chan("bs", "bluesky"), chan("ma", "mastodon"), chan("su", "substack"),
                chan("yt", "youtube"), chan("wa", "whatsapp")]

    async def gql(query, variables=None):
        if "organizations" in query:
            return {"account": {"organizations": [{"id": "o", "name": "A"}]}}
        return {"channels": channels}
    monkeypatch.setattr(publisher, "gql", gql)
    monkeypatch.setattr(publisher.get_settings(), "buffer_channel_ids", "")
    # captions are written for exactly the connected platforms (YouTube and WhatsApp are left alone)
    assert run(publisher.caption_platforms()) == ["facebook", "tiktok", "pinterest", "googlebusiness", "bluesky", "mastodon", "substack"]
    assert _copy_keys(["facebook", "tiktok", "pinterest"]) == ["title", "facebook", "tiktok", "pinterest", "tiktok_title", "pinterest_title"]

    fb_text = ("Is your warehouse safe at night? Our HD CCTV lets you watch every aisle live from your phone. " * 4
               + "\n📞 +971 55 940 8284 · 🌐 amanasoft.ae #Amanasoft #Dubai #UAE")
    caps = {"facebook": fb_text, "tiktok": "TikTok text #Dubai", "tiktok_title": "Watch your warehouse live",
            "pinterest": "Pin text amanasoft.ae", "pinterest_title": "HD CCTV for Dubai warehouses", "title": "t"}
    by = {c["id"]: c for c in channels}
    tt = publisher._input(by["tt"], caps, "https://img")
    assert tt["text"] == "TikTok text #Dubai" and tt["metadata"] == {"tiktok": {"title": "Watch your warehouse live", "isAiGenerated": True}}
    pin = publisher._input(by["pin"], caps, "https://img")["metadata"]["pinterest"]
    assert pin == {"title": "HD CCTV for Dubai warehouses", "url": "https://amanasoft.ae", "boardServiceId": "b1"}
    gb = publisher._input(by["gb"], caps, "https://img")
    assert gb["metadata"] == {"google": {"type": "whats_new", "detailsWhatsNew": {"button": "learn_more", "link": "https://amanasoft.ae"}}}
    # channels connected after the captions were written: a fitted Facebook text, within that platform's limit
    assert "+971" not in gb["text"] and "#" not in gb["text"]  # Google: no phone number, no hashtags
    bs = publisher._input(by["bs"], caps, "https://img")["text"]
    assert len(bs) <= 300 and not bs.endswith(" ")
    assert len(publisher._input(by["ma"], caps, "https://img")["text"]) <= 500

    check = _check_copy(load_brand(), ["facebook", "googlebusiness", "tiktok"])
    bad = {"title": "t", "facebook": "ok", "googlebusiness": "Call +971 55 940 8284 now #Dubai", "tiktok": "tt"}
    problems = " | ".join(check(bad))
    assert "phone number" in problems and "hashtags" in problems and "'tiktok_title' is missing" in problems


def test_typed_idea_gets_a_brand_layout_or_a_simple_photo(client):
    """A typed prompt about a service gets a full brand layout (headline, benefits, contact bar); a prompt that is
    not about a service (a limo, a greeting) stays a simple photo. Either way the idea decides the post."""
    prompts.clear()
    idea = "HD CCTV for a warehouse in Al Quoz, a technician in a white helmet fixing a camera"
    post = wait_for(client, client.post("/api/posts", json={"idea": idea}).json()["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    from app.composer import TEMPLATES
    assert post["brief"]["template"] in TEMPLATES and len(post["brief"]["benefits"]) == 3
    director = next(u for sy, u in prompts if "LAYOUT: use" in u)
    assert idea in director and "THE IDEA WINS" in director

    prompts.clear()
    idea = "Luxury limo service: a black Mercedes S-Class at the Burj Khalifa at night"
    post = wait_for(client, client.post("/api/posts", json={"idea": idea}).json()["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    assert post["brief"]["template"] == "clean"  # not an Amanasoft service: simple photo
    copy = next(sy for sy, u in prompts if "copywriter" in sy)
    assert idea in copy and "Stay on that topic" in copy

    # "Simple photo" picked in the form: the clean style even for a service
    post = client.post("/api/posts", json={"idea": "Fiber cabling in a new office", "template": "clean"}).json()
    assert wait_for(client, post["id"], ["pending_review", "failed"])["brief"]["template"] == "clean"


def test_idea_wins_in_the_branded_layouts_too(client):
    prompts.clear()
    post = client.post("/api/posts", json={"idea": "Cybersecurity for a bank head office", "template": "spotlight"}).json()
    wait_for(client, post["id"], ["pending_review", "failed"])
    user = next(u for sy, u in prompts if "LAYOUT: use" in u)
    assert "THE IDEA WINS" in user and "must follow" not in user


def test_industry_demo_is_never_emailed_or_published(client):
    prompts.clear()
    mails, published = len(sent), len(buffer_calls)
    assert client.post("/api/demos", json={"industry": "nonsense"}).status_code == 400
    demo = client.post("/api/demos", json={"industry": "limousine", "note": "a white Rolls-Royce at Atlantis"}).json()
    demo = wait_for(client, demo["id"], ["demo_ready", "failed"])
    assert demo["status"] == "demo_ready", demo["error"]
    assert len(sent) == mails and len(buffer_calls) == published  # no review email, nothing posted
    assert demo["brief"]["_industry"] == "Luxury Limousine & Chauffeur" and demo["brief"]["_contact"] == "site"
    assert demo["id"] not in [p["id"] for p in client.get("/api/posts").json()]  # kept off the Posts page
    assert demo["id"] in [p["id"] for p in client.get("/api/demos").json()]
    first = next(u for sy, u in prompts if "DEMO post" in sy)
    assert "white Rolls-Royce" in first and "SCENE for the photo" in first
    dl = client.get(f"/demos/{demo['id']}/download")
    assert dl.status_code == 200 and dl.headers["content-type"] == "image/jpeg" and "attachment" in dl.headers["content-disposition"]
    assert client.get("/demos").status_code == 200
    # the next demo for the same industry shows a different scene
    prompts.clear()
    again = client.post("/api/demos", json={"industry": "limousine"}).json()
    wait_for(client, again["id"], ["demo_ready", "failed"])
    second = next(u for sy, u in prompts if "DEMO post" in sy)
    scene = lambda text: text.split("SCENE for the photo:", 1)[1].split("\n", 1)[0]  # noqa: E731
    assert scene(first) != scene(second)


def test_demo_industry_auto_picks_the_least_used(client):
    """Auto (the default in the list) picks the industry with the fewest demos, so demos rotate by themselves."""
    from app.industries import INDUSTRIES
    made = [(p.get("prefs") or {}).get("industry") for p in client.get("/api/demos").json()]
    demo = client.post("/api/demos", json={"industry": "", "note": ""}).json()
    picked = demo["prefs"]["industry"]
    assert picked in INDUSTRIES and made.count(picked) == min(made.count(k) for k in INDUSTRIES)
    assert picked != "limousine"  # limousine already has demos from the earlier test
    wait_for(client, demo["id"], ["demo_ready", "failed"])
    page = client.get("/demos").text
    assert 'id="industry"' in page and "Auto (a different industry each time)" in page


def test_message_on_the_post_is_used_word_for_word(client):
    """Prompt → picture and topic; Message → the exact words on the picture; Service → the service it connects to."""
    prompts.clear()
    body = {"idea": "A black luxury car in front of the Burj Khalifa at night", "message": "Arrive in style. Book your chauffeur today.",
            "service": "Web Chatbot"}
    post = client.post("/api/posts", json=body).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    b = post["brief"]
    # a brand layout: the first sentence is the headline with its last word big, the rest is the line below
    assert (b["headline_top"], b["headline_highlight"], b["subheadline"]) == ("Arrive in", "style.", "Book your chauffeur today.")
    assert b["service"] == "Web Chatbot" and b["template"] != "clean"
    director = next(u for sy, u in prompts if "LAYOUT: use" in u)
    assert body["idea"] in director and "SERVICE: Web Chatbot" in director and body["message"] in director
    copy_user = next(u for sy, u in prompts if "copywriter" in sy)
    assert body["message"] in copy_user  # the captions build on the message

    # only a message: it is also the prompt
    post = client.post("/api/posts", json={"message": "Your network, always on"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["idea"] == "Your network, always on"
    assert (post["brief"]["headline_top"], post["brief"]["headline_highlight"]) == ("Your network,", "always on")

    # a branded layout keeps the message too
    post = client.post("/api/posts", json={"idea": "Fiber cabling for offices", "message": "Fast, clean, tested", "template": "split"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["brief"]["template"] == "split"
    assert (post["brief"]["headline_top"], post["brief"]["headline_highlight"]) == ("Fast, clean,", "tested")

    assert client.post("/api/posts", json={"idea": "x" * 10, "message": "y" * 151}).status_code == 400


# ---------- the client's own photos and videos ----------

vision_calls: list[tuple] = []


async def fake_vision(system, text, pictures, temperature=0.3):
    vision_calls.append((system, text, pictures))
    assert 1 <= len(pictures) <= 3  # Groq's picture models take 3 at most
    return {"what_we_see": "Two technicians in white safety helmets fix an HD camera on a warehouse wall.",
            "visible_text": "", "service": "IP CCTV & AHD CCTV Systems", "angle": "Warehouses stay watched day and night.",
            "quality": ""}, "fake/vision"


def picture(w, h, color=(40, 90, 160), fmt="JPEG", mode="RGB") -> bytes:
    from PIL import ImageDraw
    img = Image.new(mode, (w, h), color if mode == "RGB" else (*color, 0))
    ImageDraw.Draw(img).ellipse((w // 4, h // 4, w * 3 // 4, h * 3 // 4), fill=(250, 200, 60) if mode == "RGB" else (250, 200, 60, 255))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def upload(client, kind, pictures=(), **fields):
    files = [("images", (f"{n}.jpg", data, "image/jpeg")) for n, data in enumerate(pictures, 1)]
    return client.post("/api/uploads", data={"kind": kind, **fields}, files=files or None)


def test_pictures_are_made_ready_for_every_network():
    from app import uploads
    tall, info = uploads.prepare_picture(picture(1080, 1920))  # a 9:16 phone photo
    img = Image.open(io.BytesIO(tall))
    assert info["fitted"] and abs(img.width / img.height - 0.8) < 0.01  # soft background to Instagram's 4:5
    assert img.width <= 1440 and img.height <= 1800 and len(tall) <= 1_000_000 and img.format == "JPEG"
    wide, info = uploads.prepare_picture(picture(3000, 1000))
    assert info["fitted"] and abs(Image.open(io.BytesIO(wide)).width / Image.open(io.BytesIO(wide)).height - 1.91) < 0.01
    normal, info = uploads.prepare_picture(picture(1080, 1350))
    assert not info["fitted"] and Image.open(io.BytesIO(normal)).size == (1080, 1350)
    # a transparent PNG gets a white background, not a black one
    png, _ = uploads.prepare_picture(picture(800, 800, fmt="PNG", mode="RGBA"))
    assert Image.open(io.BytesIO(png)).convert("RGB").getpixel((5, 5))[0] > 240
    # a phone photo stored sideways (EXIF orientation 6) is turned upright
    side = Image.new("RGB", (1200, 900), (10, 20, 30))
    exif = side.getexif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    side.save(buf, "JPEG", exif=exif)
    _, info = uploads.prepare_picture(buf.getvalue())
    assert info["original"] == [900, 1200]
    # a carousel's later photos take the first photo's shape
    _, info = uploads.prepare_picture(picture(1200, 1200), ratio=0.8)
    assert info["ratio"] == 0.8 and info["fitted"]
    with pytest.raises(uploads.UploadError, match="too small"):
        uploads.prepare_picture(picture(200, 200))
    with pytest.raises(uploads.UploadError, match="can't be opened"):
        uploads.prepare_picture(b"not a picture")
    # many photos reach the AI as at most 3 numbered contact sheets
    assert len(uploads.pictures_for_ai([picture(600, 750)] * 10)) == 3
    assert len(uploads.pictures_for_ai([picture(600, 750)] * 2)) == 2


def test_own_photo_gets_ai_captions_email_and_is_published(client, monkeypatch):
    monkeypatch.setattr(writer, "ask_vision_json", fake_vision)
    vision_calls.clear()
    prompts.clear()
    sent.clear()
    buffer_calls.clear()
    r = upload(client, "image", [picture(1080, 1920)], note="Our team at a warehouse in Al Quoz")
    assert r.status_code == 200, r.text
    post = wait_for(client, r.json()["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    media, b = post["prefs"]["media"], post["brief"]
    assert media["kind"] == "image" and media["items"][0]["fitted"] and post["final_image_url"] == media["items"][0]["url"]
    assert b["template"] == "photo" and b["service"] == "IP CCTV & AHD CCTV Systems" and "white safety helmets" in b["what_we_see"]
    assert "Al Quoz" in vision_calls[-1][1]  # the AI saw the note along with the photo
    copy_system, copy_user = next((sy, u) for sy, u in prompts if "copywriter" in sy)
    assert "OWN photo" in copy_system and "white safety helmets" in copy_user and "public title" in copy_system
    assert "headline_top" not in copy_user  # no empty on-image fields sent to the copywriter
    mail = sent[-1]
    assert "Your own photo" in mail["html"] and "Rewrite the text" in mail["html"] and "Different image" not in mail["html"]
    page = client.get(f"/posts/{post['id']}").text
    assert "What the AI saw" in page and "Write new captions" in page and 'id="cap-title"' in page

    # rejecting rewrites the text only: the photo and what the AI saw stay
    client.post(f"/api/posts/{post['id']}/reject", json={"reason": "Shorter"})
    again = wait_for(client, post["id"], ["pending_review", "failed"])
    assert again["attempt"] == 2 and again["final_image_url"] == post["final_image_url"] and again["brief"] == post["brief"]
    assert len(vision_calls) == 1  # the photo was not looked at twice

    token = re.search(r"t=([0-9a-f]+)", links(sent[-1]["html"])["approve"]).group(1)
    client.post(f"/review/{post['id']}", data={"action": "approve", "t": token})
    assert get(client, post["id"])["status"] == "published"
    ig = next(c for c in buffer_calls if c["channelId"] == "ig1")
    assert ig["assets"] == [{"image": {"url": post["final_image_url"]}}]
    assert ig["metadata"] == {"instagram": {"type": "post", "shouldShareToFeed": True}}
    # it shows on the Posts page like any post
    assert post["id"] in [p["id"] for p in client.get("/api/posts").json()]


def test_carousel_upload_goes_out_as_one_post_with_all_photos(client, monkeypatch):
    monkeypatch.setattr(writer, "ask_vision_json", fake_vision)
    sent.clear()
    buffer_calls.clear()
    assert upload(client, "carousel", [picture(1080, 1350)]).status_code == 400  # a carousel needs 2+
    assert upload(client, "carousel", [picture(800, 800)] * 11).status_code == 400  # Instagram's limit is 10
    assert upload(client, "image", [picture(800, 800)] * 2).status_code == 400
    bad = upload(client, "carousel", [picture(1080, 1350), b"broken"])
    assert bad.status_code == 400 and "Picture 2" in bad.json()["detail"]
    shapes = [picture(1080, 1350), picture(1200, 1200), picture(1920, 1080), picture(1080, 1350), picture(900, 1600)]
    r = upload(client, "carousel", shapes)
    assert r.status_code == 200, r.text
    post = wait_for(client, r.json()["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    items = post["prefs"]["media"]["items"]
    assert len(items) == 5 and {i["ratio"] for i in items} == {0.8}  # all photos take photo 1's shape
    assert len(vision_calls[-1][2]) == 2  # 5 photos → 2 contact sheets
    assert {"pic2", "pic3", "pic4", "pic5"} <= set(sent[-1]["inline"])  # the email shows every photo
    assert "5 photos in this order" in sent[-1]["html"]
    client.post(f"/api/posts/{post['id']}/approve")
    post = get(client, post["id"])
    assert post["status"] == "published"
    by = {c["channelId"]: c for c in buffer_calls}
    assert [a["image"]["url"] for a in by["ig1"]["assets"]] == [i["url"] for i in items]  # an Instagram carousel
    assert len(by["x1"]["assets"]) == 4 and len(by["th1"]["assets"]) == 4  # X and Threads take 4 at most


def video_bytes(size=6000):
    return b"\x00\x00\x00\x18ftypmp42" + b"\x00" * size


def send_video(client, name="clip.mp4", data=None, meta=None, poster=True):
    data = video_bytes() if data is None else data
    target = client.post("/api/uploads/video-target", json={"name": name, "size": len(data)})
    if target.status_code != 200:
        return target
    t = target.json()
    assert client.put(t["url"], content=data, headers=t["headers"]).status_code == 200
    files = [("frames", (f"f{n}.jpg", picture(720, 1280), "image/jpeg")) for n in range(3)]
    if poster:
        files.append(("poster", ("cover.jpg", picture(720, 1280), "image/jpeg")))
    meta = {"duration": 42.5, "width": 1080, "height": 1920, "codec": "h264", "name": name, "cover_ms": 2500, **(meta or {})}
    return client.post("/api/uploads", data={"kind": "video", "video_path": t["path"], "video_meta": json.dumps(meta),
                                             "note": "Night shift at the data centre"}, files=files)


def test_own_video_is_checked_listened_to_and_published_as_a_reel(client, monkeypatch):
    from app import pipeline
    monkeypatch.setattr(writer, "ask_vision_json", fake_vision)
    heard = []

    async def fake_transcribe(data, filename):
        heard.append(len(data))
        return "We installed forty cameras this week and the client can watch every aisle from his phone."
    monkeypatch.setattr(pipeline, "transcribe", fake_transcribe)
    prompts.clear()
    sent.clear()
    buffer_calls.clear()
    # checks before anything is stored
    assert client.post("/api/uploads/video-target", json={"name": "clip.avi", "size": 100}).status_code == 400
    too_big = client.post("/api/uploads/video-target", json={"name": "clip.mp4", "size": 51 * 1024 * 1024})
    assert too_big.status_code == 400 and "50 MB" in too_big.json()["detail"]
    fake = send_video(client, data=b"hello, this is a text file pretending" * 50)
    assert fake.status_code == 400 and "not a real MP4" in fake.json()["detail"]
    assert send_video(client, meta={"duration": 2}).status_code == 400  # Instagram Reels: 3 s at least
    assert send_video(client, meta={"width": 200, "height": 300}).status_code == 400
    assert send_video(client, poster=False).status_code == 400
    assert client.post("/api/uploads", data={"kind": "video", "video_path": "../../etc/passwd", "video_meta": "{}"}).status_code == 400

    r = send_video(client)
    assert r.status_code == 200, r.text
    post = wait_for(client, r.json()["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    media, b = post["prefs"]["media"], post["brief"]
    assert media["video"]["duration"] == 42.5 and media["video"]["cover_ms"] == 2500 and len(media["frames"]) == 3
    assert post["final_image_url"] == media["poster"]["url"] and b["template"] == "video"
    assert heard and "forty cameras" in b["spoken_in_video"]
    copy_system, copy_user = next((sy, u) for sy, u in prompts if "copywriter" in sy)
    assert "OWN video" in copy_system and "Reel" in copy_system and "forty cameras" in copy_user
    assert "Watch the video" in sent[-1]["html"] and media["video"]["url"] in sent[-1]["html"]
    page = client.get(f"/posts/{post['id']}").text
    assert "<video" in page and "Said in the video" in page

    async def gql(query, variables=None):  # the client also connected Google Business and TikTok
        if "organizations" in query or "channels" in query:
            data = await fake_gql(query, variables)
            if "channels" in data:
                data["channels"] += [
                    {"id": "gb1", "name": "Amanasoft", "service": "googlebusiness", "isQueuePaused": False, "isDisconnected": False, "isLocked": False},
                    {"id": "tt1", "name": "amanasoft", "service": "tiktok", "isQueuePaused": False, "isDisconnected": False, "isLocked": False}]
            return data
        return await fake_gql(query, variables)
    monkeypatch.setattr(publisher, "gql", gql)
    client.post(f"/api/posts/{post['id']}/approve")
    post = get(client, post["id"])
    assert post["status"] == "published", post  # a skipped channel is not a failure
    by = {c["channelId"]: c for c in buffer_calls}
    assert "gb1" not in by  # Google Business takes no videos
    gb = next(r for r in post["publish_results"]["channels"] if r["channel_id"] == "gb1")
    assert gb["skipped"] and "doesn't take videos" in gb["error"]
    assert by["ig1"]["metadata"] == {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    assert by["ig1"]["assets"] == [{"video": {"url": media["video"]["url"], "metadata": {"thumbnailOffset": 2500}}}]
    assert by["fb1"]["assets"][0]["video"]["metadata"] == {"title": post["captions"]["title"][:100]}
    assert len(by["tt1"]["text"]) <= 150 and by["tt1"]["metadata"]["tiktok"]["isAiGenerated"] is False
    assert "Skipped" in sent[-1]["html"] and sent[-1]["subject"].startswith("✅ Published")


def test_old_videos_are_removed_from_storage_after_a_week(client, monkeypatch):
    from datetime import timedelta
    from app import pipeline
    from app.store import get_store
    from app.timeutil import iso, now_utc
    monkeypatch.setattr(writer, "ask_vision_json", fake_vision)
    r = send_video(client)
    post = wait_for(client, r.json()["id"], ["pending_review", "failed"])
    store = get_store()
    path = post["prefs"]["media"]["video"]["path"]
    assert (store.media_dir / path).exists()
    run(store.update_post(post["id"], status="published", published_at=iso(now_utc() - timedelta(days=3))))
    assert run(pipeline.remove_old_videos()) == 0 and (store.media_dir / path).exists()  # too recent
    run(store.update_post(post["id"], published_at=iso(now_utc() - timedelta(days=8))))
    assert run(pipeline.remove_old_videos()) >= 1
    after = get(client, post["id"])
    assert not (store.media_dir / path).exists() and after["prefs"]["media"]["video"]["removed"]
    assert (store.media_dir / after["prefs"]["media"]["poster"]["path"]).exists()  # the cover stays
    assert "removed from storage" in client.get(f"/posts/{post['id']}").text


def test_picture_ai_falls_back_to_gemini_then_to_the_note(monkeypatch):
    from app import imagegen
    from app.brand import load_brand
    from app.llm import LLMError

    async def no_vision(*a, **k):
        raise LLMError("Groq is down")

    async def gemini(prompt, pictures):
        return '{"what_we_see": "A server room.", "service": "Structured Cabling", "angle": "Tidy racks."}', "B"

    async def gemini_down(prompt, pictures):
        raise imagegen.ImageGenError("cookies expired")
    monkeypatch.setattr(writer, "ask_vision_json", no_vision)
    monkeypatch.setattr(imagegen.pool, "describe", gemini)
    media = {"kind": "image", "items": [{"thumb": "http://x/t.jpg"}]}
    b = run(writer.make_upload_brief(load_brand(), media, [picture(400, 500)]))
    assert b["_model"] == "gemini/B" and b["service"] == "Structured Cabling"
    monkeypatch.setattr(imagegen.pool, "describe", gemini_down)
    b = run(writer.make_upload_brief(load_brand(), media, [picture(400, 500)], note="New fiber backbone in JLT"))
    assert b["what_we_see"] == "New fiber backbone in JLT"
    with pytest.raises(LLMError, match="Press Retry"):
        run(writer.make_upload_brief(load_brand(), media, [picture(400, 500)]))


def test_silent_video_speech_is_ignored():
    from app.llm import NOT_SPEECH
    assert NOT_SPEECH.match("Thank you.") and NOT_SPEECH.match(" you ") and NOT_SPEECH.match("[Music]")
    assert not NOT_SPEECH.match("Thank you for choosing Amanasoft for your new office network")


def test_upload_page_shows_the_choices_and_channel_rules(client):
    page = client.get("/upload")
    assert page.status_code == 200
    for text in ("One photo", "Photo carousel", "Video", "Write captions &amp; send for approval", "/static/upload.js"):
        assert text in page.text, text
    rules = json.loads(re.search(r"const RULES = (\{.*?\});", page.text).group(1))
    assert rules["max_video_mb"] == 50 and rules["max_images"] == 10
    assert {c["service"] for c in rules["channels"]} >= {"facebook", "instagram", "linkedin"}
    assert 'href="/upload"' in client.get("/").text  # in the menu


# ---------- the prompt assistant ----------

def test_prompt_assistant_writes_ready_options(client, monkeypatch):
    from app import assistant
    from app.llm import LLMError
    seen = []

    async def fake(system, user, check=None, temperature=0.7):
        seen.append((system, user))
        data = {"reply": "Here are three ideas!", "options": [
            {"title": "Night watch", "prompt": " ".join(["A modern warehouse at night with HD cameras"] * 4),
             "message": "See every aisle. Day and night.", "service": "IP CCTV & AHD CCTV Systems"},
            {"title": "Made up", "prompt": " ".join(["An engineer in a white safety helmet checks a camera"] * 3),
             "message": "Smart security for your business", "service": "Rocket Science"},
        ]}
        problems = check(data)
        assert "'Rocket Science' is not in the list" in " ".join(problems)  # the AI is asked to fix it
        return data, "fake/model"
    monkeypatch.setattr(assistant, "ask_json", fake)
    home = client.get("/").text
    assert 'id="ai-fab"' in home and "/static/assistant.js" in home
    with TestClient(app) as anon:
        assert 'id="ai-fab"' not in anon.get("/login").text  # only for the logged-in dashboard
        assert anon.post("/api/assistant", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 401

    chat = [{"role": "user", "content": "cctv post for warehouses"},
            {"role": "assistant", "content": "Option 1 \"Night watch\": PROMPT: ... | MESSAGE: ..."},
            {"role": "user", "content": "make it more luxury"}]
    r = client.post("/api/assistant", json={"messages": chat})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["reply"] and len(data["options"]) == 2
    assert data["options"][0]["service"] == "IP CCTV & AHD CCTV Systems" and data["options"][1]["service"] == ""  # unknown → Auto
    assert all(len(o["message"]) <= 150 for o in data["options"])
    system, user = seen[-1]
    assert "CONVERSATION SO FAR" in user and "cctv post for warehouses" in user and "make it more luxury" in user
    assert "white safety helmets" in system and "Structured Cabling" in system and "recent posts" in system

    assert client.post("/api/assistant", json={"messages": []}).status_code == 400
    assert client.post("/api/assistant", json={"messages": [{"role": "assistant", "content": "hi"}]}).status_code == 400

    async def down(*a, **k):
        raise LLMError("all models busy")
    monkeypatch.setattr(assistant, "ask_json", down)
    busy = client.post("/api/assistant", json={"messages": [{"role": "user", "content": "hi"}]})
    assert busy.status_code == 503 and "try again" in busy.json()["detail"]


def test_browsers_always_get_the_new_style_and_scripts_after_an_update(client):
    """The page links /static/app.css?v=<content hash>: an updated file gets a new address, so no browser keeps
    an old copy (an old copy showed the new pages unstyled and hid the assistant)."""
    import hashlib
    page = client.get("/").text
    for name in ("app.css", "app.js", "assistant.js"):
        v = hashlib.md5((ROOT / "app" / "static" / name).read_bytes()).hexdigest()[:10]
        assert f"/static/{name}?v={v}" in page, name
    assert "/static/upload.js?v=" in client.get("/upload").text
    assert "/static/app.css?v=" in client.get("/login").text
    versioned = client.get(re.search(r'href="(/static/app\.css\?v=\w+)"', page).group(1))
    assert versioned.status_code == 200 and "immutable" in versioned.headers["cache-control"] and ".ai-fab" in versioned.text
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"  # an old page's plain address: always checked


# ---------- layouts: faces stay free; same picture in any layout ----------

def test_text_never_covers_a_face(client, monkeypatch):
    """The AI picture has a face at the top: the Dark photo layout (headline at the top) is swapped for one that
    keeps the face free, and the activity says so."""
    from app import composer, pipeline
    monkeypatch.setattr(pipeline, "find_faces", lambda img: [[200, 40, 300, 170]])  # fake photos are 512×512
    post = client.post("/api/posts", json={"idea": "AI receptionist for a busy clinic", "template": "spotlight"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["status"] == "pending_review", post["error"]
    b = post["brief"]
    assert b["_faces"] == [[200, 40, 300, 170]] and b["_photo_size"] == [512, 512]
    assert b["template"] != "spotlight" and composer.face_fit(b["template"], b, (512, 512)) <= pipeline.FACE_OK
    assert composer.face_fit("spotlight", b, (512, 512)) > 0.5
    assert any("would cover a face" in e["what"] for e in client.get(f"/api/posts/{post['id']}").json() and
               pipeline_events(post["id"]))
    page = client.get(f"/posts/{post['id']}").text
    assert "Try other layouts" in page and "covers a face" in page and "face found, kept free of text" in page


def pipeline_events(post_id):
    from app import pipeline
    return run(pipeline.events(post_id))


def test_same_picture_in_another_layout_from_dashboard_and_email(client):
    from app.composer import LAYOUT_NAMES
    sent.clear()
    post = client.post("/api/posts", json={"idea": "Structured cabling for a new office", "template": "wave"}).json()
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    raw, captions = post["raw_image_path"], post["captions"]
    # previews of every layout (made once, then kept)
    for layout in ("glass", "neon", "clean"):
        r = client.get(f"/posts/{post['id']}/layout/{layout}.jpg", follow_redirects=False)
        assert r.status_code == 302 and client.get(r.headers["location"].replace("http://testserver", "")).status_code == 200
    assert client.get(f"/posts/{post['id']}/layout/nonsense.jpg").status_code == 404
    with TestClient(app) as anon:
        assert anon.get(f"/posts/{post['id']}/layout/glass.jpg").status_code == 401

    # dashboard: switch to Glass → same picture and captions, a new design, a new approval email
    mails = len(sent)
    assert client.post(f"/api/posts/{post['id']}/layout", json={"layout": "glass"}).status_code == 200
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["brief"]["template"] == "glass" and post["raw_image_path"] == raw and post["captions"] == captions
    assert "-glass" in post["final_image_url"] and len(sent) == mails + 1 and post["attempt"] == 1
    assert "Layout: Glass" in sent[-1]["html"] and "Try other layouts" in sent[-1]["html"]

    # email: the "Try other layouts" link opens a page of previews; tapping one switches
    url = re.search(r'href="http://testserver(/review/[^"]+action=layouts[^"]*)"', sent[-1]["html"]).group(1).replace("&amp;", "&")
    page = client.get(url)
    assert page.status_code == 200 and "Try other layouts" in page.text and LAYOUT_NAMES["neon"] in page.text
    token = re.search(r"t=([0-9a-f]+)", url).group(1)
    assert client.get(f"/review/{post['id']}/layout/neon.jpg?t=bad").status_code == 403
    assert client.get(f"/review/{post['id']}/layout/neon.jpg?t={token}", follow_redirects=False).status_code == 302
    done = client.post(f"/review/{post['id']}", data={"action": "layouts", "t": token, "layout": "neon"})
    assert done.status_code == 200 and "Changing to Neon tech" in done.text
    post = wait_for(client, post["id"], ["pending_review", "failed"])
    assert post["brief"]["template"] == "neon" and post["captions"] == captions

    # once approved, the layout can't change any more
    client.post(f"/api/posts/{post['id']}/approve")
    assert client.post(f"/api/posts/{post['id']}/layout", json={"layout": "arch"}).status_code == 409
    late = client.post(f"/review/{post['id']}", data={"action": "layouts", "t": token, "layout": "arch"})
    assert "Already handled" in late.text and get(client, post["id"])["brief"]["template"] == "neon"


def test_message_headline_keeps_every_word():
    from app.composer import fit
    from app.writer import message_headline
    assert message_headline("YOUR SAFETY, OUR PRIORITY — SEE MORE, WORRY LESS! 🔒") == \
        ("YOUR SAFETY,", "OUR PRIORITY", "SEE MORE, WORRY LESS!")
    assert message_headline("Reliable IT Operations & Infrastructure Services") == \
        ("Reliable IT Operations &", "Infrastructure Services", "")
    assert message_headline("Cybersecurity") == ("Cybersecurity", "", "")
    # a line that is too long gets smaller, never shorter: no word is ever dropped
    f, lines = fit("Infrastructure Management Services For Every Office", 900, 300, 80, 76, 1)
    assert " ".join(lines) == "Infrastructure Management Services For Every Office"
