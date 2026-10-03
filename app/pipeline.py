"""The whole flow for one post. Every step saves its result, so a failed or interrupted post
resumes from where it stopped instead of starting over.

Post statuses:
  queued → writing → imaging → designing → emailing     (being made)
  pending_review                                        (waiting for approval)
  scheduled                                             (approved or automatic; goes out at scheduled_for)
  publishing → published | partly_published | publish_failed
  failed (making it went wrong) · missed (server was offline at the time) · cancelled
"""

import asyncio
import logging
from datetime import timedelta

from . import emailer, publisher
from .brand import load_brand
from .composer import compose
from .imagegen import pool
from .store import get_store
from .timeutil import fmt, iso, now_utc, parse
from .variety import fingerprint, looks_like_earlier, make_plan, plan_label
from .writer import image_prompt, make_brief, make_copy

log = logging.getLogger("pipeline")

WORKING = ["queued", "writing", "imaging", "designing", "emailing"]
CAN_REJECT = ["pending_review", "failed", "scheduled", "missed"]
CAN_CANCEL = WORKING + ["pending_review", "scheduled", "missed", "failed"]
CAN_PUBLISH_NOW = ["pending_review", "scheduled", "missed"]
CAN_RESCHEDULE = WORKING + ["pending_review", "scheduled", "missed", "failed"]
STAGE_LABEL = {
    "queued": "Waiting to start", "writing": "Writing headline and captions", "imaging": "Creating the image with Gemini",
    "designing": "Adding logo, headline and contact details", "emailing": "Sending the review email",
    "pending_review": "Waiting for your approval", "publishing": "Publishing through Buffer",
}
LATE_LIMIT = timedelta(hours=3)  # a scheduled post more than this late is not published by itself

_tasks: set[asyncio.Task] = set()
_running: set[str] = set()


class Stopped(Exception):
    """The post was cancelled while it was being made."""


def spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _now() -> str:
    return iso(now_utc())


async def note(post_id: str, what: str, by: str = "system") -> None:
    """Add a line to the post's activity history (shown on its page). Never breaks the flow."""
    try:
        store = get_store()
        events = list(await store.get_setting(f"events_{post_id}") or [])
        events.append({"at": _now(), "what": what, "by": by})
        await store.set_setting(f"events_{post_id}", events[-80:])
    except Exception:
        log.exception("Could not record activity for %s", post_id)


async def events(post_id: str) -> list[dict]:
    try:
        return list(await get_store().get_setting(f"events_{post_id}") or [])
    except Exception:
        return []


async def _stage(post_id: str, status: str) -> dict:
    """Move to the next making step. Raises Stopped if the post was cancelled meanwhile."""
    post = await get_store().claim(post_id, WORKING, status, stage_note=STAGE_LABEL[status], error=None)
    if not post:
        raise Stopped()
    return post


async def variety_history(current_id: str) -> list[dict]:
    """Every earlier post and every rejected version, newest first, for the no-repeat checks.
    For the post being made, only its rejected versions count (the new version must differ from them)."""
    out = []
    for p in await get_store().list_posts(300):
        if p["id"] != current_id and p.get("brief"):
            out.append({"brief": p["brief"], "captions": p.get("captions")})
        for h in reversed(p.get("history") or []):
            if h.get("brief"):
                out.append({"brief": h["brief"], "captions": h.get("captions")})
    return out


async def generate(post_id: str) -> None:
    """Idea → brief → captions → image → finished design → review email."""
    if post_id in _running:
        return
    _running.add(post_id)
    store, brand = get_store(), load_brand()
    try:
        post = await store.get_post(post_id)
        if not post or post["status"] not in WORKING:
            return
        stage = post["status"]
        try:
            history = post.get("history") or []
            feedback = {"reason": post.get("reject_reason"), "brief": history[-1].get("brief")} if history else None
            v = post["attempt"]
            past = await variety_history(post_id)
            prefs = post.get("prefs") or {}
            idea = "" if post["idea"].startswith("Auto: ") else post["idea"]

            if not post.get("brief") or not post.get("captions"):
                stage = "writing"
                post = await _stage(post_id, stage)
                plan = prefs.get("plan")
                if not plan:
                    plan = make_plan(past, prefs.get("service"), prefs.get("post_type"), post.get("template_pref"), brand)
                    prefs = {**prefs, "plan": plan}
                    post = await store.update_post(post_id, prefs=prefs)
                if not post.get("brief"):
                    brief = await make_brief(brand, idea, plan, past, feedback)
                    post = await store.update_post(post_id, brief=brief)
                captions = await make_copy(brand, idea, post["brief"], plan, past, feedback)
                post = await store.update_post(post_id, captions=captions)

            if not post.get("raw_image_path"):
                stage = "imaging"
                post = await _stage(post_id, stage)
                prompt = image_prompt(brand, post["brief"])
                raw, slot = await pool.generate(prompt)
                fp = fingerprint(raw)
                if looks_like_earlier(fp, past):
                    # almost the same picture as an earlier post: ask once more for a clearly different one
                    raw, slot = await pool.generate(
                        prompt + "\nMake it clearly different from typical stock images: a new angle, a different "
                                 "person and a different place."
                    )
                    fp = fingerprint(raw)
                path = f"{post_id}/v{v}-raw.png"
                await store.save_file(path, raw, "image/png")
                post = await store.update_post(post_id, raw_image_path=path,
                                               brief={**post["brief"], "_gemini_account": slot, "_fingerprint": fp})

            if not post.get("final_image_url"):
                stage = "designing"
                post = await _stage(post_id, stage)
                raw = await store.read_file(post["raw_image_path"])
                final = await asyncio.to_thread(compose, post["brief"]["template"], raw, post["brief"], brand)
                path = f"{post_id}/v{v}.jpg"
                url = await store.save_file(path, final, "image/jpeg")
                post = await store.update_post(post_id, final_image_path=path, final_image_url=url)

            stage = "emailing"
            post = await _stage(post_id, stage)
            image = await store.read_file(post["final_image_path"])
            when = parse(post.get("scheduled_for"))
            if (post.get("prefs") or {}).get("auto_publish") and when:
                # fully automatic: the email is only a heads-up, so a failed email must not stop the post
                try:
                    await emailer.send_review(post, image)
                    await note(post_id, f"Version {v} ready · heads-up email sent (publishes automatically {fmt(when)})")
                except Exception as e:
                    log.exception("Heads-up email for automatic post %s failed", post_id)
                    await note(post_id, f"Version {v} ready · heads-up email could not be sent ({e})")
                await store.claim(post_id, ["emailing"], "scheduled", stage_note=f"Publishes automatically · {fmt(when)}")
            else:
                await emailer.send_review(post, image)
                await note(post_id, f"Version {v} ready · review email sent")
                waiting = f"Waiting for your approval · for {fmt(when)}" if when else STAGE_LABEL["pending_review"]
                await store.claim(post_id, ["emailing"], "pending_review", stage_note=waiting)
        except Stopped:
            log.info("Post %s was cancelled while being made", post_id)
        except Exception as e:
            log.exception("Post %s failed while %s", post_id, stage)
            failed = await store.claim(post_id, WORKING, "failed", error=f"{type(e).__name__}: {e}",
                                       stage_note=f"Failed while: {STAGE_LABEL.get(stage, stage).lower()}")
            if failed:
                await note(post_id, f"Failed while {STAGE_LABEL.get(stage, stage).lower()}: {type(e).__name__}: {str(e)[:160]}")
                await emailer.alert_post_failed(failed)
    finally:
        _running.discard(post_id)


async def create(idea: str, template_pref: str | None, service: str | None = None, post_type: str | None = None,
                 scheduled_for=None, source: str = "manual", auto_publish: bool = False,
                 if_no_answer: str = "wait", by: str = "dashboard") -> dict:
    """Everything is optional: with no idea and everything on Auto, the planner picks a fresh topic.
    scheduled_for (UTC datetime) = when it should go out; None = right after approval."""
    brand = load_brand()
    prefs = {"service": service if service and brand.service(service) else None, "post_type": post_type or None,
             "source": source, "auto_publish": bool(auto_publish and scheduled_for), "if_no_answer": if_no_answer}
    idea = idea.strip()
    store = get_store()
    post = await store.create_post(idea, template_pref or None, prefs, iso(scheduled_for) if scheduled_for else None)
    if not idea:
        plan = make_plan(await variety_history(post["id"]), prefs["service"], prefs["post_type"], template_pref, brand)
        post = await store.update_post(post["id"], idea=f"Auto: {plan_label(plan)}", prefs={**prefs, "plan": plan})
    what = "Created by the daily schedule" if source == "schedule" else (
        f"Created from the idea “{idea}”" if idea else "Created with an Auto topic")
    if scheduled_for:
        what += f" · to go out {fmt(scheduled_for)}" + (" automatically" if prefs["auto_publish"] else "")
    await note(post["id"], what, "schedule" if source == "schedule" else by)
    spawn(generate(post["id"]))
    return post


async def retry(post_id: str, by: str = "dashboard") -> dict | None:
    store = get_store()
    post = await store.get_post(post_id)
    if not post:
        return None
    if post["status"] == "failed":
        post = await store.claim(post_id, ["failed"], "queued", error=None, stage_note="Retrying")
        if post:
            await note(post_id, "Retry pressed · continuing from the step that failed", by)
            spawn(generate(post_id))
        return post
    if post["status"] in ("publish_failed", "partly_published"):
        await note(post_id, "Retry pressed · publishing again to the channels that failed", by)
        spawn(_publish(post_id, ["publish_failed", "partly_published"], retry_failed_only=True))
    return post


async def reject(post_id: str, reason: str, by: str = "dashboard") -> dict | None:
    """Keep the rejected version in history and make a new one (same publish time, new plan)."""
    store = get_store()
    post = await store.get_post(post_id)
    if not post or post["status"] not in CAN_REJECT:
        return None
    history = (post.get("history") or []) + [{
        "attempt": post["attempt"], "brief": post.get("brief"), "captions": post.get("captions"),
        "final_image_url": post.get("final_image_url"), "reason": reason, "at": _now(),
    }]
    prefs = {**(post.get("prefs") or {}), "plan": None}
    post = await store.claim(
        post_id, CAN_REJECT, "queued", attempt=post["attempt"] + 1, reject_reason=reason or None,
        history=history, prefs=prefs, brief=None, captions=None, raw_image_path=None, final_image_path=None,
        final_image_url=None, error=None, stage_note="Rejected; making a new version", reviewed_at=_now(),
    )
    if post:
        await note(post_id, f"Rejected{f' (“{reason}”)' if reason else ''} → making version {post['attempt']}", by)
        spawn(generate(post_id))
    return post


async def approve(post_id: str, by: str = "dashboard") -> dict | None:
    """Approve a post. With a future publish time it waits ('scheduled'); otherwise it goes out now."""
    store = get_store()
    post = await store.get_post(post_id)
    if not post or post["status"] != "pending_review":
        return None
    when = parse(post.get("scheduled_for"))
    if when and when > now_utc() + timedelta(minutes=1):
        done = await store.claim(post_id, ["pending_review"], "scheduled", reviewed_at=_now(), error=None,
                                 stage_note=f"Approved · publishes {fmt(when)}")
        if done:
            await note(post_id, f"Approved → waits and goes out {fmt(when)}", by)
        return done
    return await _publish(post_id, ["pending_review"], why="Approved → publishing now", by=by)


async def publish_now(post_id: str, by: str = "dashboard") -> dict | None:
    return await _publish(post_id, CAN_PUBLISH_NOW, why="Publish now pressed", by=by)


async def cancel(post_id: str, by: str = "dashboard") -> dict | None:
    done = await get_store().claim(post_id, CAN_CANCEL, "cancelled", stage_note="Cancelled", error=None)
    if done:
        await note(post_id, "Cancelled · nothing will be published", by)
    return done


async def reschedule(post_id: str, when, by: str = "dashboard") -> dict | None:
    """Give a post a new publish time (UTC datetime, must be in the future)."""
    store = get_store()
    post = await store.get_post(post_id)
    if not post or post["status"] not in CAN_RESCHEDULE or when <= now_utc():
        return None
    await note(post_id, f"Publish time changed to {fmt(when)}", by)
    if post["status"] == "missed":
        return await store.claim(post_id, ["missed"], "scheduled", scheduled_for=iso(when), error=None,
                                 stage_note=f"Approved · publishes {fmt(when)}")
    stage_text = {"scheduled": f"Approved · publishes {fmt(when)}",
                  "pending_review": f"Waiting for your approval · for {fmt(when)}"}.get(post["status"], post.get("stage_note"))
    if post["status"] == "scheduled" and (post.get("prefs") or {}).get("auto_publish"):
        stage_text = f"Publishes automatically · {fmt(when)}"
    return await store.update_post(post_id, scheduled_for=iso(when), stage_note=stage_text)


async def _publish(post_id: str, from_statuses: list[str], retry_failed_only: bool = False,
                   why: str | None = None, by: str = "system") -> dict | None:
    """Publish through Buffer. The status claim makes sure a post is never published twice."""
    store = get_store()
    post = await store.claim(post_id, from_statuses, "publishing", stage_note=STAGE_LABEL["publishing"],
                             reviewed_at=_now(), error=None)
    if not post:
        return None
    if why:
        await note(post_id, why, by)
    try:
        only = None
        previous = (post.get("publish_results") or {}).get("channels", [])
        if retry_failed_only and previous:
            only = [r["channel_id"] for r in previous if not r.get("ok")]
        # a post with a publish time goes out now (its time has come); others follow PUBLISH_MODE
        mode = "shareNow" if post.get("scheduled_for") else None
        result = await publisher.publish(post["captions"], post["final_image_url"], only, mode=mode)
        if only:
            done = {r["channel_id"]: r for r in previous}
            done.update({r["channel_id"]: r for r in result["channels"]})
            result["channels"] = list(done.values())
        oks = [r["ok"] for r in result["channels"]]
        status = "published" if all(oks) else ("partly_published" if any(oks) else "publish_failed")
        post = await store.update_post(post_id, status=status, publish_results=result, error=None,
                                       published_at=_now() if any(oks) else None,
                                       stage_note={"published": "Published", "partly_published": "Some platforms failed",
                                                   "publish_failed": "Publishing failed"}[status])
    except Exception as e:
        log.exception("Publishing %s failed", post_id)
        post = await store.update_post(post_id, status="publish_failed", error=f"{type(e).__name__}: {e}",
                                       stage_note="Publishing failed")
    channels = (post.get("publish_results") or {}).get("channels", [])
    sent_to = ", ".join(f"{r['label']} ({r['name']})" for r in channels if r.get("ok"))
    problems = "; ".join(f"{r['label']} ({r['name']}): {r.get('error')}" for r in channels if not r.get("ok"))
    if (post.get("publish_results") or {}).get("dry_run"):
        sent_to += " [test mode: nothing was really posted]"
    await note(post_id, (f"Published to {sent_to}" if sent_to else "Publishing failed")
               + (f" · problems: {problems}" if problems else "") + (f" · {post['error']}" if post.get("error") else ""))
    try:
        await emailer.send_published(post)
    except Exception:
        log.exception("Could not send publish report")
    return post


async def publish_due(now=None) -> None:
    """Called every minute: send out posts whose time has come."""
    store = get_store()
    now = now or now_utc()
    for post in await store.posts_in_status(["scheduled"]):
        when = parse(post.get("scheduled_for"))
        if when and when > now:
            continue
        if when and now - when > LATE_LIMIT:
            missed = await store.claim(post["id"], ["scheduled"], "missed", error=None,
                                       stage_note=f"Missed {fmt(when)}: the server was offline. Press Publish now.")
            if missed:
                await note(post["id"], f"Missed its time ({fmt(when)}): the server was offline", "schedule")
                await emailer.alert_missed(missed)
            continue
        await _publish(post["id"], ["scheduled"], why=f"Its time came ({fmt(when) if when else 'now'}) → publishing",
                       by="schedule")
    for post in await store.posts_in_status(["pending_review"]):
        when = parse(post.get("scheduled_for"))
        prefs = post.get("prefs") or {}
        if when and when <= now and now - when <= LATE_LIMIT and prefs.get("if_no_answer") == "publish":
            await _publish(post["id"], ["pending_review"], by="schedule",
                           why=f"Not approved by its time ({fmt(when)}) → publishing anyway, as set on the Schedule page")


async def resume_after_restart() -> None:
    """Posts that were half-way when the server stopped continue automatically."""
    store = get_store()
    for post in await store.posts_in_status(WORKING):
        spawn(generate(post["id"]))
    for post in await store.posts_in_status(["publishing"]):
        await store.update_post(post["id"], status="publish_failed", stage_note="Server restarted while publishing",
                                error="The server restarted during publishing. Check Buffer before pressing Retry.")
