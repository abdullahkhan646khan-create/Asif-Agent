import asyncio
import hashlib
import importlib.util
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import assistant, emailer, pipeline, publisher, scheduler, uploads
from .brand import load_brand
from .composer import LAYOUT_NAMES, TEMPLATES
from .config import ROOT, get_settings
from .imagegen import pool
from .industries import INDUSTRIES
from .llm import LLMError
from .security import check_password, check_review_token
from .store import StoreError, get_store
from .timeutil import fmt, from_local_input, iso, now_utc, parse, to_local_input
from .variety import POST_TYPES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()
APP_DIR = ROOT / "app"
STARTED_AT = now_utc()
# what /health reports, so anyone can see from outside that the keep-awake works
HEARTBEAT = {"on": False, "every_minutes": settings.heartbeat_minutes, "last_ok": None, "last_error": None}
LAST_VISIT = {"heartbeat": None, "supabase": None, "other": None}  # who visited /health last, and when


async def heartbeat_loop() -> None:
    """Keep the server awake: Render's free plan sleeps after 15 minutes without visitors, which would stop the
    schedule. The app visits its own public /health every few minutes (that also keeps Supabase active)."""
    url = f"{settings.base_url}/health"
    if not settings.base_url.startswith("https://") or settings.heartbeat_minutes <= 0:
        log.info("Heartbeat off (only runs on a public https address)")
        return
    log.info("Heartbeat on: %s every %s minutes", url, settings.heartbeat_minutes)
    HEARTBEAT["on"] = True
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        while True:
            try:
                r = await client.get(url, params={"from": "heartbeat"})
                r.raise_for_status()
                HEARTBEAT["last_ok"], HEARTBEAT["last_error"] = iso(now_utc()), None
                log.info("Heartbeat: %s %s", r.status_code, r.text[:60])
            except Exception as e:
                HEARTBEAT["last_error"] = f"{iso(now_utc())} {e}"[:200]
                log.warning("Heartbeat could not reach %s: %s", url, e)
            await asyncio.sleep(settings.heartbeat_minutes * 60)


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


class Static(StaticFiles):
    """Pages ask for /static/app.css?v=<content hash>, so after an update the browser loads the new file at once
    (it never keeps using an old copy). Those addresses can be kept; anything else is checked every time."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        versioned = b"v=" in (scope.get("query_string") or b"")
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return resp


_static_versions: dict[str, str] = {}


def static_url(name: str) -> str:
    """/static/<name>?v=<first 10 characters of its content hash>."""
    if name not in _static_versions:
        try:
            _static_versions[name] = hashlib.md5((APP_DIR / "static" / name).read_bytes()).hexdigest()[:10]
        except OSError:
            _static_versions[name] = "0"
    return f"/static/{name}?v={_static_versions[name]}"


app = FastAPI(title="Post Agent", lifespan=lifespan, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, max_age=60 * 60 * 24 * 30, same_site="lax")
app.mount("/static", Static(directory=APP_DIR / "static"), name="static")
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
templates.env.globals["static_url"] = static_url
templates.env.globals["LAYOUT_NAMES"] = LAYOUT_NAMES
templates.env.filters["media_label"] = uploads.media_label
templates.env.filters["clock"] = uploads.clock


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

@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request):
    """Visited every few minutes by the built-in heartbeat and the Supabase keep-awake job
    (supabase/keep_awake.sql), so Render never sleeps and Supabase stays active.
    awake_since shows when the server last started: if it stays the same, the server never slept."""
    source = request.query_params.get("from")
    LAST_VISIT[source if source in LAST_VISIT else "other"] = iso(now_utc())
    info = {"version": os.environ.get("RENDER_GIT_COMMIT", "local")[:7], "awake_since": iso(STARTED_AT),
            "face_check": importlib.util.find_spec("cv2") is not None,  # OpenCV installed (not loaded here)
            "heartbeat": HEARTBEAT, "last_visit": LAST_VISIT}
    try:
        await get_store().ping()
        return {"ok": True, "store": get_store().kind, **info}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200], **info}, status_code=200)


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
    return page(request, "index.html", posts=real_posts(await get_store().list_posts(60)), layouts=TEMPLATES,
                showcase_layouts=pipeline.SHOWCASE_LAYOUTS,
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
                         reschedule=post["status"] in pipeline.CAN_RESCHEDULE,
                         switch_layout=pipeline.can_switch_layout(post)),
                layouts=pipeline.layout_options(post) if pipeline.can_switch_layout(post) else [],
                previews=await pipeline.ready_previews(post) if pipeline.can_switch_layout(post) else {})


def is_demo(post: dict) -> bool:
    return (post.get("prefs") or {}).get("mode") == "demo"


def real_posts(posts: list[dict]) -> list[dict]:
    """Posts for the Posts page: industry demos live on their own page."""
    return [p for p in posts if not is_demo(p)]


@app.get("/demos", response_class=HTMLResponse)
async def demos_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    demos = [p for p in await get_store().list_posts(300) if is_demo(p)][:60]
    return page(request, "demos.html", demos=demos, industries=INDUSTRIES)


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    """Post the client's own photo, carousel or video: the AI writes the title and captions."""
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    try:
        channels, known = await publisher.target_channels(), True
    except Exception as e:
        log.warning("Upload page: could not read the Buffer channels (%s)", e)
        channels, known = [], False
    return page(request, "upload.html", rules=uploads.page_rules(channels), channels_known=known,
                min_time=to_local_input(now_utc() + timedelta(minutes=5)))


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return page(request, "schedule.html", schedule=await scheduler.get_schedule(), days=await scheduler.overview(),
                day_names=scheduler.DAY_NAMES, prepare_choices=scheduler.PREPARE_CHOICES)


# ---------- dashboard API ----------

class NewPost(BaseModel):
    idea: str = ""
    message: str = ""  # exact words for the picture; empty = the AI writes them
    template: str | None = None
    service: str | None = None
    post_type: str | None = None
    publish_at: str | None = None  # Dubai date-time 'YYYY-MM-DDTHH:MM'; empty = right after approval
    style: str = "normal"  # normal | showcase (an infographic poster with several photos)


class Reason(BaseModel):
    reason: str = ""


class NewTime(BaseModel):
    publish_at: str


@app.get("/api/posts")
async def api_posts(request: Request, limit: int = 60):
    require_api_login(request)
    return real_posts(await get_store().list_posts(max(1, min(limit, 500))))


@app.post("/api/posts")
async def api_create(request: Request, body: NewPost):
    require_api_login(request)
    if body.idea.strip() and len(body.idea.strip()) < 3:
        raise HTTPException(400, "Write a bit more about the idea, or leave it empty for a fresh topic")
    if len(body.message.strip()) > 150:
        raise HTTPException(400, "Keep the message on the post short: at most 150 characters")
    showcase = body.style == "showcase"
    if showcase:
        template = body.template if body.template in pipeline.SHOWCASE_LAYOUTS else None
    else:
        template = body.template if body.template in TEMPLATES or body.template == "clean" else None
    post_type = body.post_type if body.post_type in POST_TYPES else None
    when = future_time(body.publish_at)
    return await pipeline.create(body.idea, template, body.service or None, post_type, scheduled_for=when,
                                 message=body.message, style="showcase" if showcase else "normal")


class NewDemo(BaseModel):
    industry: str = ""  # empty or "auto" = the industry with the fewest demos so far
    note: str = ""


@app.get("/api/demos")
async def api_demos(request: Request):
    require_api_login(request)
    return [p for p in await get_store().list_posts(300) if is_demo(p)][:60]


@app.post("/api/demos")
async def api_demo_create(request: Request, body: NewDemo):
    """An industry demo: made like a post, shown on the Demos page, never emailed or published."""
    require_api_login(request)
    industry = body.industry.strip().lower()
    if industry in ("", "auto"):
        made = [(p.get("prefs") or {}).get("industry") for p in await get_store().list_posts(300) if is_demo(p)]
        industry = min(INDUSTRIES, key=lambda k: (made.count(k), list(INDUSTRIES).index(k)))
    if industry not in INDUSTRIES:
        raise HTTPException(400, "Pick an industry from the list, or Auto")
    return await pipeline.create(body.note[:500], None, industry=industry, by="dashboard")


class VideoFile(BaseModel):
    name: str
    size: int


@app.post("/api/uploads/video-target")
async def api_video_target(request: Request, body: VideoFile):
    """Step 1 of a video post: where the browser sends the video itself (straight to storage, with progress)."""
    require_api_login(request)
    try:
        path, content_type = uploads.new_video_path(body.name, body.size)
    except uploads.UploadError as e:
        raise HTTPException(400, str(e)) from None
    return {"path": path, **await get_store().upload_target(path, content_type)}


@app.put("/api/uploads/local/{path:path}")
async def api_local_upload(request: Request, path: str):
    """Only without Supabase (on a computer for testing): the browser's video upload lands here."""
    require_api_login(request)
    store = get_store()
    if store.kind != "local" or not uploads.VIDEO_PATH.match(path):
        raise HTTPException(404)
    dest = store.media_dir / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > uploads.max_video_bytes():
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "The video is too big")
            f.write(chunk)
    return {"ok": True, "size": size}


@app.post("/api/uploads")
async def api_upload(request: Request):
    """Step 2 (or the only step for pictures): check the media, store it and start the post."""
    require_api_login(request)
    form = await request.form(max_files=uploads.MAX_IMAGES + 5, max_fields=30)
    kind = str(form.get("kind") or "")
    if kind not in uploads.KIND_NAMES:
        raise HTTPException(400, "Choose a photo, a carousel or a video")
    note = str(form.get("note") or "").strip()
    if len(note) > 600:
        raise HTTPException(400, "Keep the description short: at most 600 characters")
    when = future_time(str(form.get("publish_at") or "") or None)
    try:
        if kind == "video":
            media = await uploads.save_video(form)
        else:
            media = await uploads.save_pictures(kind, [f for f in form.getlist("images") if hasattr(f, "read")])
    except uploads.UploadError as e:
        raise HTTPException(400, str(e)) from None
    return await pipeline.create_upload(media, note, str(form.get("service") or "") or None, scheduled_for=when)


class Chat(BaseModel):
    messages: list[dict]


@app.post("/api/assistant")
async def api_assistant(request: Request, body: Chat):
    """The prompt assistant in the chat bubble: writes Prompt / Message / Service options from an idea."""
    require_api_login(request)
    msgs = [m for m in body.messages[-20:] if isinstance(m, dict)]
    if not msgs or msgs[-1].get("role") != "user" or not str(msgs[-1].get("content") or "").strip():
        raise HTTPException(400, "Write a message first")
    if len(str(msgs[-1]["content"])) > 2000:
        raise HTTPException(400, "That message is very long. Keep it under 2,000 characters.")
    recent = []
    for p in (await get_store().list_posts(20))[:15]:
        b = p.get("brief") or {}
        head = " ".join(x for x in (b.get("headline_top"), b.get("headline_highlight")) if x)
        recent.append(f"{p['idea'][:100]}" + (f" | on the post: {head}" if head else ""))
    try:
        return await assistant.reply(msgs, recent)
    except LLMError as e:
        log.warning("Assistant failed: %s", e)
        raise HTTPException(503, "The assistant is busy right now. Please try again in a minute.") from None


@app.get("/demos/{post_id}/download")
async def demo_download(request: Request, post_id: str):
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    post = await get_store().get_post(post_id)
    if not post or not post.get("final_image_path"):
        raise HTTPException(404)
    data = await get_store().read_file(post["final_image_path"])
    name = re.sub(r"[^a-z0-9]+", "-", ((post.get("brief") or {}).get("_industry") or "post").lower()).strip("-")
    return Response(data, media_type="image/jpeg",
                    headers={"Content-Disposition": f'attachment; filename="amanasoft-demo-{name}-{post_id[:6]}.jpg"'})


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


class NewLayout(BaseModel):
    layout: str


@app.post("/api/posts/{post_id}/layout")
async def api_layout(request: Request, post_id: str, body: NewLayout):
    """Same picture and text in another layout; a new approval email follows."""
    require_api_login(request)
    post = await pipeline.switch_layout(post_id, body.layout)
    if not post:
        raise HTTPException(409, "The layout can only be changed while the post is waiting for approval")
    return post


async def _preview_response(post: dict | None, layout: str):
    if not post or layout not in pipeline.layouts_for(post.get("brief")) or not pipeline.can_preview(post):
        raise HTTPException(404)
    try:
        url = await pipeline.layout_preview(post, layout)
    except Exception:
        log.exception("Layout preview %s for %s failed", layout, post["id"])
        raise HTTPException(503, "This preview could not be made") from None
    return RedirectResponse(url, status_code=302)


@app.get("/api/posts/{post_id}/previews")
async def api_previews(request: Request, post_id: str):
    """The layout previews made so far (the post page fills in the missing ones as they arrive)."""
    require_api_login(request)
    post = await get_store().get_post(post_id)
    if not post:
        raise HTTPException(404)
    return await pipeline.ready_previews(post) if pipeline.can_preview(post) else {}


@app.get("/posts/{post_id}/layout/{layout}.jpg")
async def layout_preview(request: Request, post_id: str, layout: str):
    if not logged_in(request):
        raise HTTPException(401)
    return await _preview_response(await get_store().get_post(post_id), layout)


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
    verb = {"approve": "approved", "reject": "rejected", "cancel": "cancelled", "layouts": "changed"}[action]
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
    "layouts": ["pending_review"],  # "Try other layouts" from the email
    "reject": ["pending_review", "scheduled", "missed"],
    "cancel": ["pending_review", "scheduled", "missed"],
}


async def _review_post(post_id: str, action: str, t: str) -> dict:
    post = await get_store().get_post(post_id)
    if not post or action not in REVIEW_ALLOWED or not check_review_token(post, action, t):
        raise HTTPException(403, "This link is not valid anymore. A newer version of this post may have been sent.")
    return post


def invalid_link_page(request: Request) -> HTMLResponse:
    """An old or wrong email link: same refusal (403, nothing happens), shown as a readable page."""
    resp = page(request, "message.html", title="This link is not valid anymore",
                text="A newer version of this post may have been sent. Open the latest email, or the post on the dashboard.")
    resp.status_code = 403
    return resp


# One-tap reasons offered under the Reject button in the email.
QUICK_REASONS = {"image": "Different image", "text": "Too much text", "focus": "Wrong service focus",
                 "headline": "Headline not strong", "brand": "Colours not on brand", "tone": "Different tone",
                 "short": "Shorter captions"}


@app.get("/review/{post_id}", response_class=HTMLResponse)
async def review_page(request: Request, post_id: str, action: str, t: str, r: str = ""):
    """The email button. It opens a page that performs the action right away in the browser (one tap)."""
    try:
        post = await _review_post(post_id, action, t)
    except HTTPException:
        return invalid_link_page(request)
    if post["status"] not in REVIEW_ALLOWED[action]:
        await pipeline.note(post_id, f"Email “{action}” link opened, but the post was already {post['status'].replace('_', ' ')}",
                            "email link")
        return page(request, "message.html", title="Already handled", text=await handled_text(post, action), post=post)
    if action == "layouts":  # a page of previews; nothing changes until a layout is tapped
        return page(request, "layouts.html", post=post, token=t, layouts=pipeline.layout_options(post),
                    previews=await pipeline.ready_previews(post))
    when = parse(post.get("scheduled_for"))
    future = bool(when and when > now_utc() + timedelta(minutes=1))
    label = {
        "approve": f"Approving for {fmt(when)}" if future else f"Publishing to {await publisher.target_label()}",
        "reject": "Making a new version",
        "cancel": "Cancelling this post",
    }[action]
    return page(request, "review_go.html", post=post, action=action, token=t, label=label,
                reason=QUICK_REASONS.get(r, ""))


@app.get("/review/{post_id}/layout/{layout}.jpg")
async def review_layout_preview(post_id: str, layout: str, t: str):
    """The previews on the email's "Try other layouts" page (protected by the same signed link)."""
    try:
        post = await _review_post(post_id, "layouts", t)
    except HTTPException:
        raise HTTPException(403) from None
    return await _preview_response(post, layout)


@app.post("/review/{post_id}", response_class=HTMLResponse)
async def review_submit(request: Request, post_id: str, action: str = Form(...), t: str = Form(...),
                        reason: str = Form(""), layout: str = Form("")):
    try:
        post_before = await _review_post(post_id, action, t)
    except HTTPException:
        return invalid_link_page(request)

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
    if action == "layouts":
        if post_before["status"] not in REVIEW_ALLOWED["layouts"]:
            return await already()
        post = await pipeline.switch_layout(post_id, layout, by="email link")
        if not post:
            return await already()
        return page(request, "message.html", title=f"Changing to {LAYOUT_NAMES.get(layout, layout)} ✓",
                    text="Same picture and text in the new layout. A new email with it is on its way in about a minute: "
                         "approve it there. You can close this tab.")
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
