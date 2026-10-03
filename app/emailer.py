"""Emails: the approval email, cookie alerts and publish reports.

EMAIL_MODE=apps_script  A tiny Google Apps Script in the sender Gmail sends the mail (scripts/email_relay.gs).
                        Uses normal HTTPS, so it works on Render's free plan. Recommended.
EMAIL_MODE=smtp         Gmail + App Password. Only works on your own computer (Render blocks SMTP ports).
EMAIL_MODE=gmail_api    Gmail API over HTTPS (Google Cloud project + scripts/gmail_auth.py). Also works on Render.
"""

import asyncio
import base64
import logging
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import publisher
from .brand import load_brand
from .config import get_settings
from .security import review_link
from .store import get_store
from .timeutil import fmt, now_utc, parse

log = logging.getLogger("email")
env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parent / "templates" / "emails"),
    autoescape=select_autoescape(["html"]),
)
_token_cache: dict = {}


class EmailError(RuntimeError):
    pass


def _build(to: list[str], subject: str, html: str, text: str, inline: dict[str, bytes] | None) -> EmailMessage:
    s = get_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((f"{load_brand().name} Post Agent", s.gmail_sender))
    msg["To"] = ", ".join(to)
    msg["Message-ID"] = make_msgid()
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if inline:
        html_part = msg.get_payload()[1]
        for cid, data in inline.items():
            html_part.add_related(data, "image", "jpeg", cid=f"<{cid}>")
    return msg


def _send_smtp(msg: EmailMessage):
    s = get_settings()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(s.gmail_sender, s.gmail_app_password.replace(" ", ""))
        smtp.send_message(msg)


async def _gmail_token(client: httpx.AsyncClient) -> str:
    if _token_cache.get("exp", 0) > time.time() + 60:
        return _token_cache["token"]
    s = get_settings()
    r = await client.post("https://oauth2.googleapis.com/token", data={
        "client_id": s.gmail_client_id, "client_secret": s.gmail_client_secret,
        "refresh_token": s.gmail_refresh_token, "grant_type": "refresh_token",
    })
    if r.status_code != 200:
        raise EmailError(f"Gmail API token refresh failed: {r.text[:300]}")
    data = r.json()
    _token_cache.update(token=data["access_token"], exp=time.time() + data.get("expires_in", 3000))
    return data["access_token"]


async def _send_gmail_api(msg: EmailMessage):
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    async with httpx.AsyncClient(timeout=60) as client:
        token = await _gmail_token(client)
        r = await client.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            json={"raw": raw}, headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code >= 400:
        raise EmailError(f"Gmail API send failed ({r.status_code}): {r.text[:300]}")


async def _send_apps_script(to: list[str], subject: str, html: str, text: str, inline: dict[str, bytes] | None):
    s = get_settings()
    if not (s.email_relay_url and s.email_relay_secret):
        raise EmailError("Set EMAIL_RELAY_URL and EMAIL_RELAY_SECRET (see scripts/email_relay.gs)")
    if not s.email_relay_url.startswith("https://"):
        raise EmailError("EMAIL_RELAY_URL is not set yet: it must be the https://script.google.com/.../exec link "
                         "from the email relay setup (scripts/email_relay.gs)")
    payload = {
        "secret": s.email_relay_secret, "to": ", ".join(to), "subject": subject, "html": html, "text": text,
        "from_name": f"{load_brand().name} Post Agent",
        "inline": {cid: base64.b64encode(data).decode() for cid, data in (inline or {}).items()},
    }
    # Apps Script answers with a redirect to a one-time URL that holds the result, so redirects must be followed.
    async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
        r = await client.post(s.email_relay_url, json=payload)
    try:
        data = r.json()
    except ValueError:
        raise EmailError(f"Email relay gave an unexpected answer ({r.status_code}). Is the web app deployed "
                         f"with access 'Anyone'? {r.text[:200]}") from None
    if not data.get("ok"):
        raise EmailError(f"Email relay error: {data.get('error')}")


async def send(to: list[str], subject: str, html: str, text: str, inline: dict[str, bytes] | None = None):
    s = get_settings()
    if not to:
        raise EmailError("No recipient. Set REVIEW_EMAIL_TO in .env")
    if s.email_mode == "apps_script":
        await _send_apps_script(to, subject, html, text, inline)
    else:
        if not s.gmail_sender:
            raise EmailError("No sender. Set GMAIL_SENDER in .env")
        msg = _build(to, subject, html, text, inline)
        if s.email_mode == "gmail_api":
            await _send_gmail_api(msg)
        else:
            if not s.gmail_app_password:
                raise EmailError("Set GMAIL_APP_PASSWORD in .env (or use EMAIL_MODE=gmail_api)")
            try:
                await asyncio.to_thread(_send_smtp, msg)
            except (smtplib.SMTPException, OSError) as e:
                raise EmailError(f"Gmail SMTP failed: {e}") from e
    log.info("Email sent (%s) to %s: %s", s.email_mode, ", ".join(to), subject)


# ---------- the emails ----------

async def send_review(post: dict, image_bytes: bytes):
    s = get_settings()
    brand = load_brand()
    c = post["captions"]
    when = parse(post.get("scheduled_for"))
    auto = bool((post.get("prefs") or {}).get("auto_publish") and when)
    future = bool(when and when > now_utc())
    ctx = {
        "brand": brand, "post": post, "captions": c, "brief": post["brief"],
        "approve_url": review_link(post, "approve"), "reject_url": review_link(post, "reject"),
        "cancel_url": review_link(post, "cancel"),
        "dashboard_url": f"{s.base_url}/posts/{post['id']}",
        "x_len": len(c.get("x", "")), "threads_len": len(c.get("threads", "")),
        "when_label": fmt(when) if when else None, "auto": auto, "future": future,
        "targets": await publisher.target_label(),
    }
    html = env.get_template("review.html").render(**ctx)
    lines = [f"Post: {c['title']}"]
    if auto:
        lines.append(f"This post will be published AUTOMATICALLY on {ctx['when_label']} (Dubai time).")
    elif when:
        lines.append(f"Planned for {ctx['when_label']} (Dubai time). Approve to publish it then.")
    if not auto:
        lines.append(f"APPROVE: {ctx['approve_url']}")
    lines.append(f"REJECT & MAKE A NEW VERSION: {ctx['reject_url']}")
    if when:
        lines.append(f"CANCEL (publish nothing): {ctx['cancel_url']}")
    text = "\n".join(lines) + f"\n\nFACEBOOK:\n{c['facebook']}\n\nX:\n{c['x']}\n\nTHREADS:\n{c['threads']}\n"
    version = f" (version {post['attempt']})" if post["attempt"] > 1 else ""
    if auto:
        subject = f"Auto-post {fmt(when)}: {c['title']}{version}"
    elif when:
        subject = f"Approve for {fmt(when)}: {c['title']}{version}"
    else:
        subject = f"Review post: {c['title']}{version}"
    await send(s.review_recipients, subject, html, text, {"post": image_bytes})


async def send_published(post: dict):
    s = get_settings()
    results = post.get("publish_results") or {}
    html = env.get_template("published.html").render(
        brand=load_brand(), post=post, results=results.get("channels", []),
        dashboard_url=f"{s.base_url}/posts/{post['id']}",
    )
    channels = results.get("channels", [])
    ok = [r for r in channels if r.get("ok")]
    text = "\n".join(f"{r['label']} ({r['name']}): {'OK' if r.get('ok') else 'FAILED - ' + str(r.get('error'))}"
                     for r in channels) or str(post.get("error") or "")
    title = post["captions"]["title"]
    names = ", ".join(f"{r['label']} ({r['name']})" for r in ok)
    if channels and len(ok) == len(channels):
        subject = f"✅ Published to {names}: {title}"
    elif ok:
        subject = f"⚠️ Partly published ({len(ok)}/{len(channels)}): {title}"
    else:
        subject = f"❌ Publishing failed: {title}"
    if results.get("dry_run"):
        subject = "[TEST MODE, nothing posted] " + subject
    await send(s.review_recipients, subject, html, text)


async def _throttled(key: str, hours: float) -> bool:
    """True if an alert with this key was already sent recently."""
    store = get_store()
    last = await store.get_setting(f"alert_sent_{key}")
    now = datetime.now(timezone.utc)
    if last and now - datetime.fromisoformat(last) < timedelta(hours=hours):
        return True
    await store.set_setting(f"alert_sent_{key}", now.isoformat())
    return False


async def send_alert(key: str, subject: str, heading: str, lines: list[str], throttle_hours: float = 12):
    s = get_settings()
    try:
        if await _throttled(key, throttle_hours):
            return
        html = env.get_template("alert.html").render(
            brand=load_brand(), heading=heading, lines=lines, dashboard_url=s.base_url,
        )
        await send(s.alert_recipients, subject, html, heading + "\n\n" + "\n".join(lines))
    except Exception:
        log.exception("Could not send alert email %s", key)


async def alert_cookie_dead(slot: str, error: str):
    await send_alert(
        f"cookie_{slot}", f"⚠️ Gemini cookie {slot} stopped working",
        f"Gemini account {slot} needs a fresh cookie",
        [
            f"Reason: {error}",
            "Posts keep working on the other account if it is still OK.",
            "To fix: in FIREFOX open a private window → log in to that Google account → open gemini.google.com.",
            "Press F12 → Storage → Cookies → https://gemini.google.com → copy __Secure-1PSID and __Secure-1PSIDTS.",
            f"Put them in GEMINI_{slot}_1PSID and GEMINI_{slot}_1PSIDTS: in the .env file on your computer, or on Render "
            "in the service's Environment tab. Then restart the app (on Render: Save → it redeploys by itself).",
            "Close the private window WITHOUT logging out.",
        ],
    )


async def alert_missed(post: dict):
    s = get_settings()
    await send_alert(
        f"missed_{post['id']}_{post['attempt']}", f"⏰ Scheduled post was not published: {post['captions']['title'][:50]}",
        "A scheduled post missed its time",
        [f"It was planned for {fmt(post.get('scheduled_for'))} (Dubai time).",
         "The server was offline at that time (check that the 10-minute heartbeat is running).",
         f"Open it and press Publish now, or give it a new time: {s.base_url}/posts/{post['id']}"],
        throttle_hours=0,
    )


async def alert_post_failed(post: dict):
    s = get_settings()
    await send_alert(
        f"post_failed_{post['id']}_{post['attempt']}", f"❌ Post failed: {post['idea'][:50]}",
        "A post could not be finished",
        [f"Idea: {post['idea']}", f"Problem: {post.get('error')}",
         f"Open it and press Retry when fixed: {s.base_url}/posts/{post['id']}"],
        throttle_hours=0,
    )
