-- Unified Agent Platform schema (fixed)
create extension if not exists pgcrypto;

-- ── Organizations ───────────────────────────────
create table if not exists organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

-- ── User Profiles ───────────────────────────────
create table if not exists profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  org_id uuid not null references organizations(id) on delete cascade,
  role text not null check (role in ('admin','operator','viewer')),
  created_at timestamptz not null default now()
);

-- ── Agents ──────────────────────────────────────
create table if not exists agents (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  slug text not null,
  display_name text not null,
  enabled boolean not null default true,
  health_status text not null default 'unknown',
  created_at timestamptz not null default now(),
  unique(org_id, slug)
);

-- ── Agent Runs ──────────────────────────────────
create table if not exists agent_runs (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  agent_id uuid not null references agents(id) on delete cascade,
  created_by uuid not null references auth.users(id),
  status text not null check (status in ('queued','running','stopping','succeeded','failed','cancelled')),
  current_step text,
  stream_url text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now()
);

-- ── Agent Run Events ────────────────────────────
create table if not exists agent_run_events (
  id bigint generated always as identity primary key,
  run_id uuid not null references agent_runs(id) on delete cascade,
  agent_id uuid not null references agents(id) on delete cascade,
  event_type text not null,
  payload jsonb not null default '{}'::jsonb,
  created_by uuid references auth.users(id),
  created_at timestamptz not null default now()
);

-- ── Agent Schedules ─────────────────────────────
create table if not exists agent_schedules (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  agent_id uuid not null references agents(id) on delete cascade,
  created_by uuid not null references auth.users(id),
  cron text not null,
  timezone text not null default 'Asia/Kolkata',
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);

-- ── User Secrets ────────────────────────────────
create table if not exists user_secrets (
  user_id uuid primary key references auth.users(id) on delete cascade,
  org_id uuid not null references organizations(id) on delete cascade,
  encrypted_payload text not null,
  updated_at timestamptz not null default now()
);

-- ═══════════════════════════════════════════════
-- ROW LEVEL SECURITY
-- ═══════════════════════════════════════════════
alter table profiles enable row level security;
alter table agents enable row level security;
alter table agent_runs enable row level security;
alter table agent_run_events enable row level security;
alter table agent_schedules enable row level security;
alter table user_secrets enable row level security;

create policy profiles_self_org on profiles
for select using (id = auth.uid());

create policy agents_org_read on agents
for select using (
  exists (
    select 1 from profiles p where p.id = auth.uid() and p.org_id = agents.org_id
  )
);

create policy runs_org_read on agent_runs
for select using (
  exists (
    select 1 from profiles p where p.id = auth.uid() and p.org_id = agent_runs.org_id
  )
);

create policy run_events_org_read on agent_run_events
for select using (
  exists (
    select 1
    from agent_runs r
    join profiles p on p.org_id = r.org_id
    where p.id = auth.uid() and r.id = agent_run_events.run_id
  )
);

create policy schedules_org_read on agent_schedules
for select using (
  exists (
    select 1 from profiles p where p.id = auth.uid() and p.org_id = agent_schedules.org_id
  )
);

create policy secrets_owner_rw on user_secrets
for all using (user_id = auth.uid())
with check (user_id = auth.uid());

-- ═══════════════════════════════════════════════
-- REALTIME PUBLICATION
-- ═══════════════════════════════════════════════
alter publication supabase_realtime add table agent_runs;
alter publication supabase_realtime add table agent_run_events;

-- ═══════════════════════════════════════════════
-- AUTO-CREATE PROFILE ON SIGNUP
-- ═══════════════════════════════════════════════
create or replace function public.handle_new_user()
returns trigger as $$
begin
  insert into public.profiles (id, org_id, role)
  values (
    new.id,
    '00000000-0000-0000-0000-000000000001',
    'admin'
  );
  return new;
end;
$$ language plpgsql security definer;

-- Drop if exists to make this idempotent
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- ═══════════════════════════════════════════════
-- SEED DATA
-- ═══════════════════════════════════════════════
insert into organizations (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Primary Organization')
on conflict do nothing;

insert into agents (org_id, slug, display_name, enabled, health_status)
values ('00000000-0000-0000-0000-000000000001', 'shortly-ai-news', 'Shortly AI News Agent', true, 'healthy')
on conflict (org_id, slug) do nothing;

-- ══════════════════════════════════════════════════
-- PUBLISHED ARTICLES (cross-run / cross-deployment dedup)
-- ══════════════════════════════════════════════════
create table if not exists public.published_articles (
    id           bigserial primary key,
    url          text not null unique,
    title        text,
    story_key    text,
    image_url    text,
    published_at timestamptz not null default now()
);

create index if not exists idx_pub_articles_url
    on public.published_articles (url);

create index if not exists idx_pub_articles_story_key
    on public.published_articles (story_key)
    where story_key is not null;

create index if not exists idx_pub_articles_image_url
    on public.published_articles (image_url)
    where image_url is not null;

-- Worker-only table — accessed server-side via service role key only.
alter table public.published_articles disable row level security;
