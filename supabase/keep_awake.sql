-- Keep the Render server awake, from inside Supabase (free, no extra account).
--
-- Render's free plan puts the server to sleep after 15 minutes without visitors. While it sleeps the daily
-- schedule cannot run, so no posts are made. This job visits the server every 5 minutes, and also wakes it up
-- if it ever falls asleep.
--
-- How to use: Supabase → SQL Editor → New query → paste this whole file → Run.
-- Running it again is safe: it only updates the job.

create extension if not exists pg_net with schema extensions;
create extension if not exists pg_cron with schema pg_catalog;

select cron.schedule(
  'keep-render-awake',
  '*/5 * * * *',
  $$ select net.http_get('https://asif-agent.onrender.com/health?from=supabase', timeout_milliseconds := 60000) $$
);

-- Check that it runs (wait 5 minutes first):
--   select status, start_time from cron.job_run_details order by start_time desc limit 5;
--   select status_code, created from net._http_response order by created desc limit 5;
-- Stop it:
--   select cron.unschedule('keep-render-awake');
