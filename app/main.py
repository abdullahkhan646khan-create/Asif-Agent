import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import emailer, pipeline, publisher, scheduler
from .brand import load_brand
from .composer import TEMPLATES
from .config import ROOT, get_settings
from .imagegen import pool
from .security import check_password, check_review_token
from .store import StoreError, get_store
from .timeutil import fmt, from_local_input, now_utc, parse, to_local_input
from .variety import POST_TYPES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()
APP_DIR = ROOT / "app"


async def heartbeat_loop() -> None:
    """Keep the server awake: Render's free plan sleeps after 15 minutes without visitors, which would stop the
    schedule. The app visits its own public /health every few minutes (that also keeps Supabase active)."""
    url = f"{settings.base_url}/health"
    if not settings.base_url.startswith("https://") or settings.heartbeat_minutes <= 0:
        log.info("Heartbeat off (only runs on a public https address)")
        return
    log.info("Heartbeat on: %s every %s minutes", url, settings.heartbeat_minutes)
    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            await asyncio.sleep(settings.heartbeat_minutes * 60)
            try:
                r = await client.get(url)
                log.info("Heartbeat: %s %s", r.status_code, r.text[:60])
            except Exception as e:
                log.warning("Heartbeat could not reach %s: %s", url, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    get_store()
    try:
        await pipeline.resume_after_restart()
    except Exception:
        log.exception("Could not resume unfinished posts (is the database set up?)")
    tasks = [asyncio.create_task(pool.health_loop()), asyncio.create_task(scheduler.loop()),
             asyncio.create_task(heartbeat_loop())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Post Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, max_age=60 * 60 * 24 * 30, same_site="lax")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.mount("/brand-assets", StaticFiles(directory=load_brand().folder), name="brand")
if not settings.use_supabase:
    (settings.data_dir / "media").mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.data_dir / "media"), name="media")

templates = Jinja2Templates(directory=APP_DIR / "templates")


@app.exception_handler(StoreError)
async def store_error(request: Request, exc: StoreError):
    missing = "PGRST205" in str(exc) or "schema cache" in str(exc)
    text = ("The database tables don't exist yet. In Supabase open SQL Editor, paste the file supabase/schema.sql "
            "and press Run, then reload this page.") if missing else f"Database problem: {exc}"
    log.error("Store error on %s: %s", request.url.path, exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": text}, status_code=503)
    return templates.TemplateResponse(request, "message.html", {"brand": load_brand(), "settings": settings,
                                      "now_dubai": fmt(now_utc()), "title": "Database not ready", "text": text},
                                      status_code=503)


def _ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)
    secs = int((datetime.now(dt.tzinfo) - dt).total_seconds())
    for unit, n in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit} ago"
    return "just now"


templates.env.filters["ago"] = _ago
templates.env.filters["dubai"] = fmt
templates.env.filters["dubai_time"] = lambda v: fmt(v, date=False)
templates.env.filters["local_input"] = to_local_input


def page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"brand": load_brand(), "settings": settings,
                                                      "now_dubai": fmt(now_utc()), **ctx})


def logged_in(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require_api_login(request: Request):
    if not logged_in(request):
        raise HTTPException(401, "Please log in")


def future_time(value: str | None):
    """A Dubai date-time from a form field → UTC datetime in the future (or None when empty)."""
    if not value:
        return None
    try:
        when = from_local_input(value)
    except ValueError:
        raise HTTPException(400, "That date and time is not valid") from None
    if when <= now_utc() + timedelta(minutes=1):
        raise HTTPException(400, "Pick a time in the future (Dubai time)")
    return when


# ---------- heartbeat ----------

@app.get("/health")
async def health():
    """Visited every 10 minutes by the built-in heartbeat (and optionally cron-job.org) to keep Render awake and Supabase active."""
    try:
        await get_store().ping()
        return {"ok": True, "store": get_store().kind}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=200)


# ---------- login ----------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return page(request, "login.html", error=None)


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if check_password(password):
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)
    return page(request, "login.html", error="Wrong password")


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- dashboard pages ----------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return page(request, "index.html", posts=await get_store().list_posts(60), layouts=TEMPLATES,
                post_types=POST_TYPES, min_time=to_local_input(now_utc() + timedelta(minutes=5)))


@app.get("/posts/{post_id}", response_class=HTMLResponse)
async def post_page(request: Request, post_id: str):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    post = await get_store().get_post(post_id)
    if not post:
        raise HTTPException(404)
    when = parse(post.get("scheduled_for"))
    return page(request, "post.html", post=post, when=when, future=bool(when and when > now_utc()),
                targets=await publisher.target_label(), events=list(reversed(await pipeline.events(post_id))),
                min_time=to_local_input(now_utc() + timedelta(minutes=5)),
                can=dict(reject=post["status"] in pipeline.CAN_REJECT, cancel=post["status"] in pipeline.CAN_CANCEL,
                         publish_now=post["status"] in pipeline.CAN_PUBLISH_NOW,
                         reschedule=post["status"] in pipeline.CAN_RESCHEDULE))


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return page(request, "schedule.html", schedule=await scheduler.get_schedule(), days=await scheduler.overview(),
                day_names=scheduler.DAY_NAMES, prepare_choices=scheduler.PREPARE_CHOICES)


# ---------- dashboard API ----------

class NewPost(BaseModel):
    idea: str = ""
    template: str | None = None
    service: str | None = None
    post_type: str | None = None
    publish_at: str | None = None  # Dubai date-time 'YYYY-MM-DDTHH:MM'; empty = right after approval


class Reason(BaseModel):
    reason: str = ""


class NewTime(BaseModel):
    publish_at: str


@app.get("/api/posts")
async def api_posts(request: Request, limit: int = 60):
    require_api_login(request)
    return await get_store().list_posts(max(1, min(limit, 500)))


@app.post("/api/posts")
async def api_create(request: Request, body: NewPost):
    require_api_login(request)
    if body.idea.strip() and len(body.idea.strip()) < 3:
        raise HTTPException(400, "Write a bit more about the idea, or leave it empty for a fresh topic")
    template = body.template if body.template in TEMPLATES else None
    post_type = body.post_type if body.post_type in POST_TYPES else None
    when = future_time(body.publish_at)
    return await pipeline.create(body.idea, template, body.service or None, post_type, scheduled_for=when)


@app.get("/api/posts/{post_id}")
async def api_post(request: Request, post_id: str):
    require_api_login(request)
    post = await get_store().get_post(post_id)
    if not post:
        raise HTTPException(404)
    return post


@app.post("/api/posts/{post_id}/approve")
async def api_approve(request: Request, post_id: str):
    require_api_login(request)
    post = await pipeline.approve(post_id)
    if not post:
        raise HTTPException(409, "This post is not waiting for approval (maybe it was already published)")
    return post


@app.post("/api/posts/{post_id}/publish-now")
async def api_publish_now(request: Request, post_id: str):
    require_api_login(request)
    post = await pipeline.publish_now(post_id)
    if not post:
        raise HTTPException(409, "This post can't be published right now")
    return post


@app.post("/api/posts/{post_id}/cancel")
async def api_cancel(request: Request, post_id: str):
    require_api_login(request)
    post = await pipeline.cancel(post_id)
    if not post:
        raise HTTPException(409, "This post can't be cancelled anymore")
    return post


@app.post("/api/posts/{post_id}/reschedule")
async def api_reschedule(request: Request, post_id: str, body: NewTime):
    require_api_login(request)
    post = await pipeline.reschedule(post_id, future_time(body.publish_at))
    if not post:
        raise HTTPException(409, "This post's time can't be changed now")
    return post


@app.post("/api/posts/{post_id}/reject")
async def api_reject(request: Request, post_id: str, body: Reason):
    require_api_login(request)
    post = await pipeline.reject(post_id, body.reason.strip())
    if not post:
        raise HTTPException(409, "This post can't be rejected right now")
    return post


@app.post("/api/posts/{post_id}/retry")
async def api_retry(request: Request, post_id: str):
    require_api_login(request)
    post = await pipeline.retry(post_id)
    if not post:
        raise HTTPException(404)
    return post


@app.post("/api/posts/{post_id}/resend")
async def api_resend(request: Request, post_id: str):
    require_api_login(request)
    post = await get_store().get_post(post_id)
    if not post or post["status"] not in ("pending_review", "scheduled"):
        raise HTTPException(409, "Only posts waiting for approval or scheduled can be re-sent")
    await emailer.send_review(post, await get_store().read_file(post["final_image_path"]))
    return {"ok": True}


@app.get("/api/schedule")
async def api_get_schedule(request: Request):
    require_api_login(request)
    return await scheduler.get_schedule()


@app.post("/api/schedule")
async def api_save_schedule(request: Request):
    require_api_login(request)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise scheduler.ScheduleError("Send the schedule as an object")
        saved = await scheduler.save_schedule(body)
    except ValueError as e:  # ScheduleError, or a body that is not JSON
        raise HTTPException(400, str(e) if isinstance(e, scheduler.ScheduleError) else "Invalid schedule data") from None
    await scheduler.prepare_due()  # a time that is already within its prepare window starts right away
    return saved


# ---------- email button pages (no login; protected by the signed link) ----------

async def handled_text(post: dict, action: str) -> str:
    """Plain words for 'this email button can't do that anymore', saying what already happened."""
    st = post["status"]
    verb = {"approve": "approved", "reject": "rejected", "cancel": "cancelled"}[action]
    when = parse(post.get("scheduled_for"))
    last = (await pipeline.events(post["id"]) or [{}])[-1]
    last_txt = f" Last step: {last.get('what')} ({last.get('by')}, {fmt(last.get('at'))})." if last.get("what") else ""
    if st == "published":
        return (f"This post was already published{(' on ' + fmt(post['published_at'])) if post.get('published_at') else ''} "
                f"to {await publisher.target_label()}, so it can't be {verb} from this email anymore.{last_txt}")
    if st == "scheduled":
        return f"This post is already approved. It goes out on {fmt(when)} (Dubai time). Nothing else to do.{last_txt}"
    if st == "cancelled":
        return f"This post was cancelled, so nothing will be published.{last_txt}"
    if st in pipeline.WORKING:
        return "A new version of this post is being made right now. You'll get a new email for it in 1-3 minutes."
    if st == "failed":
        return f"Making this post failed ({post.get('error') or 'unknown error'}). Open it on the dashboard and press Retry."
    if st in ("publish_failed", "partly_published"):
        return f"Publishing this post had a problem ({post.get('error') or 'see the dashboard'}). Open it on the dashboard to retry."
    return f"This post is now: {post.get('stage_note') or st}.{last_txt}"


REVIEW_ALLOWED = {
    "approve": ["pending_review"],
    "reject": ["pending_review", "scheduled", "missed"],
    "cancel": ["pending_review", "scheduled", "missed"],
}


async def _review_post(post_id: str, action: str, t: str) -> dict:
    post = await get_store().get_post(post_id)
    if not post or action not in REVIEW_ALLOWED or not check_review_token(post, action, t):
        raise HTTPException(403, "This link is not valid anymore. A newer version of this post may have been sent.")
    return post


# One-tap reasons offered under the Reject button in the email.
QUICK_REASONS = {"image": "Different image", "text": "Too much text", "focus": "Wrong service focus",
                 "headline": "Headline not strong", "brand": "Colours not on brand"}


@app.get("/review/{post_id}", response_class=HTMLResponse)
async def review_page(request: Request, post_id: str, action: str, t: str, r: str = ""):
    """The email button. It opens a page that performs the action right away in the browser (one tap)."""
    post = await _review_post(post_id, action, t)
    if post["status"] not in REVIEW_ALLOWED[action]:
        await pipeline.note(post_id, f"Email “{action}” link opened, but the post was already {post['status'].replace('_', ' ')}",
                            "email link")
        return page(request, "message.html", title="Already handled", text=await handled_text(post, action), post=post)
    when = parse(post.get("scheduled_for"))
    future = bool(when and when > now_utc() + timedelta(minutes=1))
    label = {
        "approve": f"Approving for {fmt(when)}" if future else f"Publishing to {await publisher.target_label()}",
        "reject": "Making a new version",
        "cancel": "Cancelling this post",
    }[action]
    return page(request, "review_go.html", post=post, action=action, token=t, label=label,
                reason=QUICK_REASONS.get(r, ""))


@app.post("/review/{post_id}", response_class=HTMLResponse)
async def review_submit(request: Request, post_id: str, action: str = Form(...), t: str = Form(...),
                        reason: str = Form("")):
    post_before = await _review_post(post_id, action, t)

    async def already():
        now_post = await get_store().get_post(post_id) or post_before
        return page(request, "message.html", title="Already handled", text=await handled_text(now_post, action), post=now_post)

    if action == "approve":
        post = await pipeline.approve(post_id, by="email link")
        if not post:
            return await already()
        if post["status"] == "scheduled":
            return page(request, "message.html", title="Approved ✓",
                        text=f"It will be published on {fmt(post['scheduled_for'])} (Dubai time). You can close this tab.",
                        post=post)
        ok = post["status"] == "published"
        return page(request, "message.html", title="Published ✓" if ok else "There was a problem publishing",
                    text=(f"Your post is live on {await publisher.target_label()}." if ok else
                          f"{post.get('error') or 'See the details below.'} Open the dashboard to retry."),
                    post=post, results=(post.get("publish_results") or {}).get("channels", []))
    if action == "cancel":
        post = await pipeline.cancel(post_id, by="email link")
        if not post:
            return await already()
        return page(request, "message.html", title="Cancelled ✓",
                    text="Nothing will be published for this post. You can close this tab.")
    post = await pipeline.reject(post_id, reason.strip(), by="email link")
    if not post:
        return await already()
    why = f" (reason: {reason.strip()})" if reason.strip() else ""
    return page(request, "message.html", title="Making a new version ✓",
                text=f"Got it{why}. A new version is being made and will be emailed to you in 1-3 minutes. "
                     "You can close this tab.")
