# Roadmap

## Phase 0: Client research ✅ done
- [x] Read amanasoft.ae: services, contact details, taglines, team.
- [x] Studied the 4 sample posters: colours, layout style, and their mistakes (wrong domain, wrong phone, typo).
- [x] Saved the logo files exactly as served by the website (colour, white and icon).
- [x] Wrote the brand kit: `brand/amanasoft/profile.json` and `PROFILE.md`.
- [ ] LinkedIn "About" text for Mirza A. Asif. LinkedIn blocks automatic reading, so paste it into `profile.json`.

## Phase 1: Core system ✅ built
- [x] Dashboard: login, "What should we post today?", live progress, post pages, Schedule.
- [x] Writer: creative director + copywriter (Groq first, OpenRouter backup, automatic checks).
- [x] Gemini image step with 2 cookie accounts, automatic switch, and an alert email when a cookie dies.
- [x] Designer with 3 brand layouts (wave, spotlight, checklist): real logo, exact contact details, no AI text.
- [x] Approval email with Approve / Reject buttons (signed, one-time links).
- [x] Reject with a reason: makes a new version and emails it again.
- [x] Buffer publishing to Facebook, X and Threads, with a per-platform report and "retry failed only".
- [x] Supabase storage (or a local folder), `/health` heartbeat, resume after restart.
- [x] 4 layouts incl. the "Dubai night" poster with a code-drawn UAE flag; 3-benefit strip; no button text on images.
- [x] Input: optional idea + Service + Post type + Layout; everything on Auto = fresh topic.
- [x] No-repeat engine: rotation of topic/layout/person/place/shot/light/caption style + text and picture similarity checks.
- [x] Publish at a chosen time (Dubai) + daily Schedule page (times, days, ask-first or fully automatic,
      prepare time, publish-anyway option, 7-day overview); Publish now / Change time / Cancel; missed-post alerts.
- [x] Dashboard: filters (needs approval, scheduled, published, problems), show older posts, mobile layout.
- [x] Automated tests (27) + a full browser walkthrough of every page and button (43 checks).

## Phase 2: Live test ⏳ next (you)
0. **Biggest risk first: does Gemini work from Render?** Deploy to Render with only the login password and 2 fresh
   Firefox cookies filled in, then create one post from the dashboard. About 15 minutes. If the image fails, try
   `GEMINI_PROXY` or pick another image source before building on it.
1. Fill in `.env` (Groq, OpenRouter, 2 Gemini cookies, email relay, your Gmail, Buffer). Keep `DRY_RUN_PUBLISH=true`.
2. Create a post and check that the review email arrives (`scripts/check_gemini.py` tests Gemini alone).
3. Create 5–10 posts with different ideas and check the images, text and emails.
4. Tune `profile.json` (tone, services, words to avoid) until the posts feel right.
5. Add Supabase, set `DRY_RUN_PUBLISH=false` and publish one real post.

## Phase 3: Go live on Render
1. Private GitHub repo → Render Blueprint → fill in the environment values.
2. Email keeps working as is (`apps_script` relay, same as on your computer).
3. Set up the cron-job.org heartbeat every 10 minutes on `/health`.
4. Publish one real post from your phone through the email buttons.

## Phase 4: Later upgrades (when the basics are stable)
- AI quality check of every image before the email is sent (redo automatically if it's bad).
- Learning from Approve / Reject history, so posts improve over time.
- Two designs per post (A/B) to choose from in the email.
- Post at the best time (Buffer queue / schedule) instead of right away.
- More layouts: festival greeting, before/after, carousel.
- Arabic posts (Arabic fonts and right-to-left layout).
- WhatsApp approvals or WhatsApp Status.
- Multiple clients from one dashboard.
