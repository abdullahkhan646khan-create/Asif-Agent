-- Run once in Supabase: Dashboard → SQL Editor → New query → paste → Run.

create table if not exists public.posts (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  idea text not null,
  template_pref text,
  status text not null default 'queued',
  stage_note text,
  attempt int not null default 1,
  brief jsonb,
  captions jsonb,
  raw_image_path text,
  final_image_path text,
  final_image_url text,
  reject_reason text,
  history jsonb not null default '[]'::jsonb,
  publish_results jsonb,
  error text,
  reviewed_at timestamptz,
  published_at timestamptz,
  prefs jsonb not null default '{}'::jsonb,
  scheduled_for timestamptz
);

-- for projects created before these columns existed
alter table public.posts add column if not exists prefs jsonb not null default '{}'::jsonb;
alter table public.posts add column if not exists scheduled_for timestamptz;

create index if not exists posts_created_at_idx on public.posts (created_at desc);
create index if not exists posts_status_idx on public.posts (status);
create index if not exists posts_scheduled_idx on public.posts (scheduled_for);

create table if not exists public.settings (
  key text primary key,
  value jsonb,
  updated_at timestamptz not null default now()
);

-- The server uses the service_role key; make sure it may use the tables (newer projects don't grant this automatically).
grant usage on schema public to service_role;
grant all on table public.posts, public.settings to service_role;

-- Row Level Security on with no policies: only the server (service/secret key) can read or write.
alter table public.posts enable row level security;
alter table public.settings enable row level security;

-- Public image bucket: Buffer must be able to download the finished post images.
insert into storage.buckets (id, name, public)
values ('posts', 'posts', true)
on conflict (id) do nothing;

-- Let the API see the new tables right away.
notify pgrst, 'reload schema';
