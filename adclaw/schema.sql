-- AdClaw Schema — Supabase (Moltbot project)
-- Run once: paste into Supabase SQL editor or use apply_migration

-- -----------------------------------------------------------------------
-- Advertisers & wallets
-- -----------------------------------------------------------------------
create table if not exists adclaw_advertisers (
  id              uuid primary key default gen_random_uuid(),
  email           text not null unique,
  display_name    text,
  wallet_credits  integer not null default 0 check (wallet_credits >= 0),
  is_subscribed   boolean not null default false,
  sub_expires_at  timestamptz,
  stripe_customer_id text,
  created_at      timestamptz not null default now()
);

-- -----------------------------------------------------------------------
-- Projects (advertiser inputs for card generation)
-- -----------------------------------------------------------------------
create table if not exists adclaw_projects (
  id              uuid primary key default gen_random_uuid(),
  advertiser_id   uuid not null references adclaw_advertisers(id) on delete cascade,
  name            text not null,
  github_url      text,
  webpage_url     text,
  description     text,
  notes           text,
  ad_goal         text,
  target_audience text,
  created_at      timestamptz not null default now()
);

-- -----------------------------------------------------------------------
-- Campaigns
-- -----------------------------------------------------------------------
create table if not exists adclaw_campaigns (
  id              uuid primary key default gen_random_uuid(),
  advertiser_id   uuid not null references adclaw_advertisers(id) on delete cascade,
  name            text not null,
  budget_credits  integer not null default 0 check (budget_credits >= 0),
  spent_credits   integer not null default 0 check (spent_credits >= 0),
  status          text not null default 'draft' check (status in ('draft','active','paused','exhausted')),
  created_at      timestamptz not null default now()
);

-- -----------------------------------------------------------------------
-- Slides (individual 20-sec ad units inside a campaign)
-- -----------------------------------------------------------------------
create table if not exists adclaw_slides (
  id              uuid primary key default gen_random_uuid(),
  campaign_id     uuid not null references adclaw_campaigns(id) on delete cascade,
  slide_type      text not null default 'feature' check (slide_type in ('feature','upgrade','tip','insight','project')),
  badge           text,
  accent_color    text default '#f59e0b',
  headline        text not null,
  body            text not null,
  cta_label       text,
  cta_url         text,
  status          text not null default 'pending' check (status in ('pending','active','paused','rejected')),
  last_served_at  timestamptz,
  created_at      timestamptz not null default now()
);

create index if not exists adclaw_slides_active_served
  on adclaw_slides (status, last_served_at asc nulls first)
  where status = 'active';

-- -----------------------------------------------------------------------
-- Impressions (view events — written by AdClaw server on fetch)
-- -----------------------------------------------------------------------
create table if not exists adclaw_impressions (
  id            bigserial primary key,
  slide_id      uuid not null references adclaw_slides(id) on delete cascade,
  campaign_id   uuid not null,
  instance_id   text not null,   -- War Room instance that served the slide
  ip_hash       text not null,   -- sha256[:16] of viewer IP (privacy-safe)
  tenant_id     text not null default 'default',
  viewed_at     timestamptz not null default now()
);

create index if not exists adclaw_impressions_campaign on adclaw_impressions (campaign_id, viewed_at desc);
create index if not exists adclaw_impressions_slide    on adclaw_impressions (slide_id, viewed_at desc);

-- -----------------------------------------------------------------------
-- War Room instances (registered on startup)
-- -----------------------------------------------------------------------
create table if not exists adclaw_instances (
  instance_id   text primary key,
  tenant_id     text not null default 'default',
  public_url    text,
  last_seen     timestamptz not null default now(),
  registered_at timestamptz not null default now()
);

-- -----------------------------------------------------------------------
-- Atomic credit deduction (called per impression)
-- Returns false and does nothing if campaign has insufficient credits.
-- -----------------------------------------------------------------------
create or replace function adclaw_deduct_credits(
  p_campaign_id uuid,
  p_credits     integer
) returns boolean
language plpgsql as $$
declare
  v_advertiser_id uuid;
  v_wallet        integer;
begin
  select advertiser_id into v_advertiser_id
    from adclaw_campaigns where id = p_campaign_id;
  if not found then return false; end if;

  -- Lock advertiser row
  select wallet_credits into v_wallet
    from adclaw_advertisers where id = v_advertiser_id for update;

  if v_wallet < p_credits then
    -- Pause campaign when budget runs out
    update adclaw_campaigns set status = 'exhausted' where id = p_campaign_id;
    return false;
  end if;

  -- Deduct from wallet
  update adclaw_advertisers
     set wallet_credits = wallet_credits - p_credits
   where id = v_advertiser_id;

  -- Track spend on campaign
  update adclaw_campaigns
     set spent_credits = spent_credits + p_credits
   where id = p_campaign_id;

  return true;
end;
$$;

-- -----------------------------------------------------------------------
-- Credit top-up (called by Stripe webhook)
-- -----------------------------------------------------------------------
create or replace function adclaw_topup_credits(
  p_advertiser_id uuid,
  p_credits       integer
) returns void
language plpgsql as $$
begin
  update adclaw_advertisers
     set wallet_credits = wallet_credits + p_credits
   where id = p_advertiser_id;
  if not found then
    raise exception 'Advertiser % not found', p_advertiser_id;
  end if;
end;
$$;

-- -----------------------------------------------------------------------
-- Slide edit tracking (added 2026-04-21)
-- -----------------------------------------------------------------------
alter table adclaw_slides add column if not exists edits_today integer not null default 0;
alter table adclaw_slides add column if not exists last_edited_at timestamptz;

-- -----------------------------------------------------------------------
-- Project webpage + notes (added 2026-04-21)
-- -----------------------------------------------------------------------
alter table adclaw_projects add column if not exists webpage_url text;
alter table adclaw_projects add column if not exists notes text;
alter table adclaw_projects add column if not exists voice text default 'af_bella';
