"""Make the system brand new for the client: deletes every post, its pictures, the activity logs,
the sent-alert records and the list of handled posting times in Supabase.

Kept: the Gemini cookie state (so image making keeps working) and the posting times of the schedule.
The schedule is switched OFF; turn it on again on the Schedule page when the client is ready.

    .venv/bin/python scripts/reset_for_delivery.py          # only shows what would be deleted
    .venv/bin/python scripts/reset_for_delivery.py --yes    # deletes it
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import get_settings  # noqa: E402

WORKING = {"queued", "writing", "imaging", "designing", "emailing", "publishing"}
TEST_SETTINGS = ("events_", "alert_sent_", "schedule_slots_done")  # setting keys that belong to old posts


def main():
    s = get_settings()
    if not s.use_supabase:
        sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY are not set in .env; nothing to reset.")
    headers = {"apikey": s.supabase_service_key}
    if s.supabase_service_key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {s.supabase_service_key}"
    c = httpx.Client(base_url=s.supabase_url.rstrip("/"), headers=headers, timeout=60)
    bucket = s.supabase_bucket

    def ok(r):
        if r.status_code >= 400:
            sys.exit(f"Supabase error {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    posts = ok(c.get("/rest/v1/posts", params={"select": "id,status"}))
    settings = ok(c.get("/rest/v1/settings", params={"select": "key"}))
    old_keys = [x["key"] for x in settings if x["key"].startswith(TEST_SETTINGS)]
    files = []
    for folder in ok(c.post(f"/storage/v1/object/list/{bucket}", json={"prefix": "", "limit": 10000})):
        inside = ok(c.post(f"/storage/v1/object/list/{bucket}", json={"prefix": folder["name"] + "/", "limit": 1000}))
        files += [f"{folder['name']}/{f['name']}" for f in inside] if inside else [folder["name"]]

    print(f"Posts:            {len(posts)}")
    print(f"Pictures:         {len(files)} files")
    print(f"Old-post records: {len(old_keys)} (activity logs, sent alerts, handled posting times)")
    print("Kept:             Gemini cookie state; schedule times (schedule switched off)")
    busy = [p["id"] for p in posts if p["status"] in WORKING]
    if busy:
        sys.exit(f"\n{len(busy)} post(s) are being made or published right now. Wait a few minutes and run again.")
    if "--yes" not in sys.argv:
        print("\nNothing deleted. Run again with --yes to delete.")
        return

    # 1. schedule off first, so no new post starts during the reset; past times are never made late
    schedule = ok(c.get("/rest/v1/settings", params={"key": "eq.schedule", "select": "value"}))
    if schedule:
        value = {**schedule[0]["value"], "enabled": False, "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        ok(c.post("/rest/v1/settings", json={"key": "schedule", "value": value, "updated_at": value["saved_at"]},
                  headers={"Prefer": "resolution=merge-duplicates"}))
    # 2. pictures, 3. posts, 4. records that belonged to the posts
    for i in range(0, len(files), 500):
        ok(c.request("DELETE", f"/storage/v1/object/{bucket}", json={"prefixes": files[i:i + 500]}))
    ok(c.delete("/rest/v1/posts", params={"id": "not.is.null"}))
    for prefix in TEST_SETTINGS:
        ok(c.delete("/rest/v1/settings", params={"key": f"like.{prefix}*"}))

    left_posts = ok(c.get("/rest/v1/posts", params={"select": "id"}))
    left_files = ok(c.post(f"/storage/v1/object/list/{bucket}", json={"prefix": "", "limit": 10}))
    print(f"\nDone. Posts left: {len(left_posts)} · picture folders left: {len(left_files)} · schedule: off")


main()
