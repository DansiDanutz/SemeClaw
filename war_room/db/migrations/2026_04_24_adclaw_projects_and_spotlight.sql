-- ════════════════════════════════════════════════════════════════════════
-- ad-NOW — projects + spotlight tables (gap-fill migration)
-- ════════════════════════════════════════════════════════════════════════
-- Backend code references adclaw_projects and adclaw_spotlight throughout
-- (war_room/dashboard/routes/advertiser.py and several other migrations
-- ALTER them) but the tables themselves were never created in any prior
-- migration. This migration is idempotent — safe to re-run.

-- ── adclaw_projects: an advertiser's "ad sources" (a repo or product)
create table if not exists adclaw_projects (
  id              uuid primary key default gen_random_uuid(),
  advertiser_id   uuid not null references adclaw_advertisers(id) on delete cascade,
  name            text not null,
  github_url      text,
  webpage_url     text,
  description     text,
  status          text not null default 'active',
  notes           text,
  ad_goal         text,
  target_audience text,
  problem_solved  text,
  tagline         text,
  voice           text default 'af_bella',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists adclaw_projects_adv_idx on adclaw_projects(advertiser_id);

-- Forward-compat: if a prior partial deploy created the table without the
-- newer columns, add them.
alter table adclaw_projects
  add column if not exists status          text not null default 'active',
  add column if not exists notes           text,
  add column if not exists problem_solved  text,
  add column if not exists tagline         text,
  add column if not exists voice           text default 'af_bella';

-- ── adclaw_spotlight: the rotating "Spotlight" anchor (Gold/Diamond perk)
create table if not exists adclaw_spotlight (
  id            bigserial primary key,
  user_id       uuid not null references adclaw_advertisers(id) on delete cascade,
  project_id    uuid references adclaw_projects(id) on delete cascade,
  featured_from timestamptz not null default now(),
  ttl_days      int  not null default 7,
  created_at    timestamptz not null default now()
);
create index if not exists adclaw_spotlight_user_idx on adclaw_spotlight(user_id);
create index if not exists adclaw_spotlight_active_idx
  on adclaw_spotlight(featured_from desc);

-- Sanity probe — should print 2 created tables + their column counts.
select 'adclaw_projects'  as table_name, count(*) as columns
  from information_schema.columns where table_name = 'adclaw_projects'
union all
select 'adclaw_spotlight', count(*)
  from information_schema.columns where table_name = 'adclaw_spotlight';
