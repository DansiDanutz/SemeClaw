-- ═══════════════════════════════════════════════════════════════════════
-- ad-NOW — base schema (run this BEFORE any other adclaw migration)
-- ═══════════════════════════════════════════════════════════════════════

create extension if not exists "pgcrypto";

create table if not exists adclaw_advertisers (
  id              uuid primary key default gen_random_uuid(),
  email           text unique,
  display_name    text,
  wallet_credits  int  not null default 0,
  stripe_customer text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create table if not exists adclaw_campaigns (
  id              uuid primary key default gen_random_uuid(),
  advertiser_id   uuid not null references adclaw_advertisers(id) on delete cascade,
  name            text not null default 'Untitled campaign',
  status          text not null default 'active',
  budget_credits  int  not null default 0,
  spent_credits   int  not null default 0,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists adclaw_campaigns_adv_idx on adclaw_campaigns(advertiser_id);

create table if not exists adclaw_slides (
  id              uuid primary key default gen_random_uuid(),
  campaign_id     uuid not null references adclaw_campaigns(id) on delete cascade,
  slide_type      text not null default 'feature',
  status          text not null default 'draft',
  badge           text,
  accent_color    text default '#f59e0b',
  headline        text not null,
  body            text,
  cta_label       text,
  cta_url         text,
  last_served_at  timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);
create index if not exists adclaw_slides_status_idx   on adclaw_slides(status);
create index if not exists adclaw_slides_campaign_idx on adclaw_slides(campaign_id);

create table if not exists adclaw_impressions (
  id          bigserial primary key,
  slide_id    uuid references adclaw_slides(id)    on delete set null,
  campaign_id uuid references adclaw_campaigns(id) on delete set null,
  instance_id text,
  ip_hash     text,
  tenant_id   text,
  viewed_at   timestamptz not null default now()
);
create index if not exists adclaw_impressions_slide_idx on adclaw_impressions(slide_id);
create index if not exists adclaw_impressions_camp_idx  on adclaw_impressions(campaign_id);
create index if not exists adclaw_impressions_ip_idx    on adclaw_impressions(ip_hash, viewed_at desc);

create table if not exists adclaw_credit_transactions (
  id            bigserial primary key,
  advertiser_id uuid not null references adclaw_advertisers(id) on delete cascade,
  type          text not null,
  credits       int  not null,
  description   text,
  slide_id      uuid references adclaw_slides(id)    on delete set null,
  campaign_id   uuid references adclaw_campaigns(id) on delete set null,
  instance_id   text,
  created_at    timestamptz not null default now()
);
create index if not exists adclaw_ctx_adv_idx      on adclaw_credit_transactions(advertiser_id, created_at desc);
create index if not exists adclaw_ctx_type_idx     on adclaw_credit_transactions(type);
create index if not exists adclaw_ctx_desc_adv_idx on adclaw_credit_transactions(advertiser_id, description);

create table if not exists adclaw_instances (
  instance_id text primary key,
  tenant_id   text,
  public_url  text,
  last_seen   timestamptz not null default now(),
  created_at  timestamptz not null default now()
);

create or replace function adclaw_frequency_cap(
  p_ip_hash   text,
  p_hours     int default 4,
  p_max_views int default 3
) returns table (slide_id uuid)
  language sql stable as $$
    select i.slide_id
      from adclaw_impressions i
     where i.ip_hash = p_ip_hash
       and i.viewed_at >= now() - (p_hours || ' hours')::interval
     group by i.slide_id
    having count(*) >= p_max_views;
$$;
grant execute on function adclaw_frequency_cap(text, int, int) to anon, authenticated, service_role;

create or replace function adclaw_deduct_credits(
  p_campaign_id uuid,
  p_credits     int,
  p_slide_id    uuid default null,
  p_instance_id text default null,
  p_description text default 'Impression'
) returns jsonb language plpgsql security definer as $$
declare v_adv uuid;
begin
  select advertiser_id into v_adv from adclaw_campaigns where id = p_campaign_id for update;
  if v_adv is null then return jsonb_build_object('ok', false, 'error', 'campaign not found'); end if;
  update adclaw_advertisers
     set wallet_credits = greatest(0, wallet_credits - p_credits)
   where id = v_adv and wallet_credits >= p_credits;
  if not found then return jsonb_build_object('ok', false, 'error', 'insufficient credits'); end if;
  update adclaw_campaigns set spent_credits = spent_credits + p_credits where id = p_campaign_id;
  insert into adclaw_credit_transactions
    (advertiser_id, type, credits, description, slide_id, campaign_id, instance_id)
    values (v_adv, 'spend', -p_credits, p_description, p_slide_id, p_campaign_id, p_instance_id);
  return jsonb_build_object('ok', true);
end; $$;
grant execute on function adclaw_deduct_credits(uuid, int, uuid, text, text) to service_role;

create or replace function adclaw_topup_credits(
  p_advertiser_id uuid,
  p_credits       int,
  p_description   text default 'Topup'
) returns jsonb language plpgsql security definer as $$
begin
  update adclaw_advertisers set wallet_credits = wallet_credits + p_credits where id = p_advertiser_id;
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'topup', p_credits, p_description);
  return jsonb_build_object('ok', true);
end; $$;
grant execute on function adclaw_topup_credits(uuid, int, text) to service_role;
