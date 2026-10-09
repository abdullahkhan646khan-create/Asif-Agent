# Amanasoft Post Agent

Type an idea → the AI writes the post and makes the image → you get an email → **Approve** publishes to Facebook, X and Threads through Buffer; **Reject** makes a new version.

```
Dashboard idea
  → ① Creative director (Groq, OpenRouter backup): post type, layout, headline, image description
  → ② Copywriter: Facebook / X / Threads captions (length, phone and domain checked)
  → ③ Gemini (cookie account A, then B): the picture, with no text in it
  → ④ Designer (our code): logo, headline, benefits and contact footer on a brand layout
  → ⑤ Email to you with Approve / Reject buttons
  → Approve: Buffer → Facebook, X, Threads    Reject: back to ① with your reason
```

## How a post is requested

On the dashboard everything is optional:
- **Idea:** free text, e.g. "CCTV for warehouses". Roman Urdu works too; posts come out in English.
- **Service / Post type:** pick from the menus.
- **Layout:** Blue wave, Dark photo, Checklist or Dubai night.

Leave everything on **Auto** and the agent picks a topic that hasn't been posted yet.

**When it's published:**
- **Right after approval** (default)
- **At a time you choose** (Dubai time): you approve before that time and it waits.

On each post's page you can **Publish now**, **Change the time**, **Reject & redo** or **Cancel**.

## Approving from the email (one tap)

- **✅ Approve:** one tap publishes the post, or schedules it if it has a set time.
- **❌ Reject:** one tap makes a new version and emails it again. You can also tap a quick reason under the button (Different image, Too much text, Wrong service, Weak headline, Not on brand) and the AI uses it.
- **What you see:** a browser tab opens and shows the result right away. There is no second confirmation click.
- **Safety:** opening the link alone does nothing. The tab sends the action from your browser, so email virus scanners that "pre-open" links can't publish anything.
- **Old links** (for a post that was already published, rejected or cancelled) explain what happened instead of doing anything.

## Daily schedule (Schedule page)

- **Posting times:** set the times (Dubai) and days, e.g. 09:00, 13:00 and 19:00, Monday to Sunday.
- **Each post is made ahead of time** (15 minutes to 12 hours before its time, you choose). The topic is picked fresh each time, with no repeats.
- **Two modes:**
  - **Ask me first** (default): you get the approval email; approved posts go out exactly at their time. If you don't answer in time, you choose between **wait for me** and **publish anyway**.
  - **Fully automatic:** no approval needed. You still get an email with **Cancel** and **Reject & redo** before the post goes out.
- **The server checks every minute** (`app/scheduler.py`). It must be awake, which the 10-minute heartbeat ensures on Render.
- **If the server was offline at a posting time:**
  - it still prepares a post that is up to 1 hour late
  - an approved post that is more than 3 hours late is marked **Missed**, you get an email, and you choose **Publish now** or a new time
- **Times that had already passed** when you saved the schedule are never posted late.

**No repeats** (`app/variety.py`):
- Every post gets a plan: service, post type, industry, layout, person, place, camera shot, light and caption style. Each is the option used longest ago.
- New headlines, benefits, captions and picture descriptions are compared with **all** earlier posts, including rejected versions. If anything is too similar, the AI rewrites it.
- Each picture gets a fingerprint. A picture that looks like an earlier one is made again.

## Folder map

| Path | What it is |
|---|---|
| `brand/amanasoft/profile.json` | The client's brand kit the AI follows (edit to change tone, services, colours…) |
| `brand/amanasoft/PROFILE.md` | The same profile, readable, with sources and open questions |
| `brand/amanasoft/logo/` | Original logo files from amanasoft.ae (never redrawn) |
| `app/` | The application (FastAPI) |
| `app/composer.py` | The 3 post layouts: **wave**, **spotlight**, **checklist** |
| `supabase/schema.sql` | Database setup for Supabase |
| `scripts/` | Helpers: preview layouts, Gmail API token, list Buffer channels |
| `tests/` | Full-flow test with fake services |

## 1. Run it on your computer

```bash
cd "Asif A.Agent"
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in .env (see section 2)
.venv/bin/uvicorn app.main:app --reload
```

Open http://localhost:8000 and log in with `DASHBOARD_PASSWORD`.

Leave `DRY_RUN_PUBLISH=true` for the first tests. Approve then does everything except actually posting.
On your computer you can leave the Supabase fields empty; posts and images are then kept in `data/`.
Buffer needs a **public** image link, though, so to really publish you need Supabase (or the Render deploy).

**Check Gemini without making a post:** `.venv/bin/python scripts/check_gemini.py` logs in and makes two test pictures.

## 2. Getting each key

**Groq** (free): https://console.groq.com/keys → create key → `GROQ_API_KEY`.

**OpenRouter** (backup, free models): https://openrouter.ai/settings/keys → `OPENROUTER_API_KEY`.

**Gemini cookies** (two extra Google accounts, A and B). **Use Firefox**: Chrome, Edge and Brave now tie these cookies to the computer, so on a server they die within 1–2 hours.
1. In Firefox, open a private window (Cmd/Ctrl + Shift + P), log in to the extra account and open https://gemini.google.com.
2. Press F12 → **Storage** → **Cookies** → `https://gemini.google.com`.
3. Copy `__Secure-1PSID` and `__Secure-1PSIDTS` into `GEMINI_A_...` (then repeat with the second and third accounts for `GEMINI_B_...` and `GEMINI_C_...`; they are used in that order).
4. Close the window **without logging out**.
5. Use each cookie in **one place only**. Take fresh cookies for Render instead of reusing the ones from your computer.

When a cookie expires later, you get an email. Put the new values in `.env` (on Render: the service's **Environment** tab) and restart the app.
A new cookie you put there always replaces the app's saved copy.

**Email** (`EMAIL_MODE=apps_script`, works the same on your computer and on Render):
Render's free plan blocks the normal way of sending email (SMTP). So the app hands each email over HTTPS to a tiny Google Apps Script that lives in the sender Gmail, and the script sends it.
1. Open `scripts/email_relay.gs` and follow the 5 steps at its top.
2. You get a Web app URL. Put it in `EMAIL_RELAY_URL`, and put the secret in `EMAIL_RELAY_SECRET`.
3. Set `REVIEW_EMAIL_TO` to your own Gmail. The first review email shows that it works.

Other options: `EMAIL_MODE=smtp` with a Gmail App Password (your computer only), or `EMAIL_MODE=gmail_api` (steps at the top of `scripts/gmail_auth.py`).

**Supabase** (free):
1. Create a project at https://supabase.com.
2. Open the **SQL Editor**, paste `supabase/schema.sql` and press **Run**.
3. Go to **Project Settings → API** and copy the **Project URL** into `SUPABASE_URL` and the **service_role / secret key** into `SUPABASE_SERVICE_KEY`. This key is for the server only; never share it.

**Buffer**:
1. Connect the Facebook page, X account and Threads account in Buffer.
2. Create a key at https://publish.buffer.com/settings/api and put it in `BUFFER_API_KEY`.
3. Check which channels were found:
```bash
.venv/bin/python scripts/buffer_channels.py
```

## 3. Check the design without any keys

```bash
.venv/bin/python scripts/preview_templates.py                 # uses a sample photo
.venv/bin/python scripts/preview_templates.py my-photo.jpg    # or any photo
```
Results are written to `data/previews/`.

```bash
.venv/bin/python -m pip install pytest && .venv/bin/python -m pytest -q
```
This runs the whole flow with fake services.

## 4. Put it on Render (after it works on your computer)

1. Push this folder to a **private** GitHub repo. `.env` is ignored by git, so your keys stay out of it.
2. On Render, choose **New → Blueprint** and pick the repo. `render.yaml` sets everything up.
3. In the service's **Environment** tab:
   - Fill in the empty values.
   - Set `PUBLIC_BASE_URL` to the Render address (e.g. `https://amanasoft-post-agent.onrender.com`).
   - Paste **fresh Firefox cookies**, not the ones used on your computer.
4. **First check:** create one post on the dashboard. If it reaches "Waiting for approval" with a picture, Gemini works on Render.
   - If Google blocks Render's address, set `GEMINI_PROXY` (a paid proxy costs a few dollars a month) and test again.
5. Keep it awake:
   - At https://cron-job.org (free), create a job that opens `https://<your-app>.onrender.com/health` every **10 minutes**.
   - This also keeps Supabase from pausing.
   - One free service running 24/7 uses about 730–744 of Render's 750 free hours a month, so run only this one service.
6. Optional: give it a nice address like `posts.amanasoft.ae` (Render's free plan includes 2 custom domains with automatic HTTPS).

The dashboard and the AI agent are **one** app on purpose. Every dashboard button calls the agent, and the email buttons open pages on the same server, so splitting them adds work without making anything faster.

## Good to know

- **Gemini cookies** break Google's terms of use. Use the two extra accounts only, never a personal one. The image step is isolated in `app/imagegen.py`, so it can be swapped for a paid API later.
- **License:** `gemini-webapi` is AGPL-3.0. If other people (e.g. the client) use this app over the internet, AGPL requires offering them the source code.
- **X limit:** X counts links as 23 characters and emoji as 2. Captions are checked and trimmed to fit.
- **Double posting:** a post can only be published once. Clicking Approve twice, or using an old email link, is refused.
