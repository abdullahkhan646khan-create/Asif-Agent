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
    "DRY_RUN_PUBLISH": "false", "PUBLISH_MODE": "shareNow",
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


def words(rnd, k):
    return " ".join(rnd.sample(VOCAB, k))


async def fake_ask_json(system, user, check=None, temperature=0.7):
    """Fake AI that writes different text every call, like a real one would."""
    import random
    counter["n"] += 1
    rnd = random.Random(counter["n"])
    if "creative director" in system:
        layout = re.search(r"LAYOUT: use '(\w+)'", user).group(1)
        data = {
            "post_type": "service_promo", "service": "Structured Cabling", "angle": words(rnd, 8),
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
    assert {c["service"] for c in post["publish_results"]["channels"]} == {"facebook", "twitter", "threads"}
    assert {c["channelId"] for c in buffer_calls} == {"fb1", "x1", "th1"}
    fb = next(c for c in buffer_calls if c["channelId"] == "fb1")
    assert fb["metadata"] == {"facebook": {"type": "post"}} and fb["assets"][0]["image"]["url"] == post["final_image_url"]
    assert next(c for c in buffer_calls if c["channelId"] == "x1")["text"] == post["captions"]["x"]
    assert sent[-1]["subject"].startswith("✅ Published to")

    # clicking Approve again must not publish twice
    again = client.post(f"/review/{post['id']}", data={"action": "approve", "t": token})
    assert "Already handled" in again.text and len(buffer_calls) == 3


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
    assert client.get(old["approve"]).status_code == 403  # links from the old email stop working


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
    monkeypatch.setattr(main.settings, "public_base_url", "https://asif-agent.onrender.com")
    monkeypatch.setattr(main.settings, "heartbeat_minutes", 0.001)  # ~0.06 s for the test

    async def go():
        task = asyncio.create_task(main.heartbeat_loop())
        await asyncio.sleep(0.3)
        task.cancel()
    run(go())
    assert visits and all(v == "https://asif-agent.onrender.com/health?from=heartbeat" for v in visits)
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
    assert len(TEMPLATES) >= 8
    for t in TEMPLATES:
        img = Image.open(io.BytesIO(compose(t, buf.getvalue(), {"template": t, **content}, load_brand())))
        assert img.size == ((1080, 1350) if t in ("skyline", "cards") else (1080, 1080)), t


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
