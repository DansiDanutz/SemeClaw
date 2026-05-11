-- SemeClaw v1.0 schema additions.
--
-- Run against the team Supabase project AFTER existing semeclaw_tasks
-- migrations are in place. Idempotent — safe to re-run.
--
-- Tables:
--   semeclaw_tenants_v1       — multi-tenant identity + plan + Stripe link
--   semeclaw_webhooks_v1      — outbound webhook subscriptions
--   semeclaw_audit_v1         — append-only audit log (mirrors data/v1/audit/*)
--   semeclaw_usage_v1         — per-tenant per-period counters
--
-- All tables are RLS-disabled by default; layer policies on top per project.

create extension if not exists "pgcrypto";

-- --------------------------------------------------------------------------
-- Tenants
-- --------------------------------------------------------------------------
create table if not exists semeclaw_tenants_v1 (
  id                  text primary key,
  name                text not null,
  api_key_hash        text not null,
  plan                text not null check (plan in ('free', 'pro', 'enterprise')),
  status              text not null default 'active'
                        check (status in ('active', 'suspended', 'deleted')),
  stripe_customer_id  text,
  branding            jsonb not null default '{}'::jsonb,
  last_billing_event  text,
  created_at          timestamptz not null default now(),
  deleted_at          timestamptz
);

create index if not exists semeclaw_tenants_v1_api_key_hash_idx
  on semeclaw_tenants_v1 (api_key_hash);
create index if not exists semeclaw_tenants_v1_stripe_customer_idx
  on semeclaw_tenants_v1 (stripe_customer_id) where stripe_customer_id is not null;

-- --------------------------------------------------------------------------
-- Webhook subscriptions
-- --------------------------------------------------------------------------
create table if not exists semeclaw_webhooks_v1 (
  id                  text primary key,
  tenant_id           text not null references semeclaw_tenants_v1(id) on delete cascade,
  url                 text not null,
  secret              text not null,
  events              text[] not null default array['*']::text[],
  status              text not null default 'active'
                        check (status in ('active', 'disabled')),
  last_delivery_at    timestamptz,
  last_status         int,
  created_at          timestamptz not null default now()
);

create index if not exists semeclaw_webhooks_v1_tenant_idx
  on semeclaw_webhooks_v1 (tenant_id);

-- --------------------------------------------------------------------------
-- Audit log
-- --------------------------------------------------------------------------
create table if not exists semeclaw_audit_v1 (
  id                  uuid primary key default gen_random_uuid(),
  ts                  timestamptz not null default now(),
  request_id          text,
  tenant_id           text,
  actor               text,
  route               text,
  method              text,
  status              int,
  request_hash        text,
  latency_ms          int,
  client_ip           text
);

create index if not exists semeclaw_audit_v1_tenant_ts_idx
  on semeclaw_audit_v1 (tenant_id, ts desc);
create index if not exists semeclaw_audit_v1_route_ts_idx
  on semeclaw_audit_v1 (route, ts desc);

-- --------------------------------------------------------------------------
-- Usage counters
-- --------------------------------------------------------------------------
create table if not exists semeclaw_usage_v1 (
  tenant_id           text not null references semeclaw_tenants_v1(id) on delete cascade,
  period              text not null, -- YYYY-MM
  meetings            int  not null default 0,
  tts_chars           int  not null default 0,
  llm_tokens          int  not null default 0,
  interventions       int  not null default 0,
  updated_at          timestamptz not null default now(),
  primary key (tenant_id, period)
);

-- --------------------------------------------------------------------------
-- RPC helpers (optional — match the patterns used by war_room/tasks/_db.py)
-- --------------------------------------------------------------------------
create or replace function semeclaw_v1_increment_usage(
  p_tenant_id text,
  p_period    text,
  p_meetings      int default 0,
  p_tts_chars     int default 0,
  p_llm_tokens    int default 0,
  p_interventions int default 0
) returns void
language plpgsql as $$
begin
  insert into semeclaw_usage_v1 (tenant_id, period, meetings, tts_chars, llm_tokens, interventions, updated_at)
  values (p_tenant_id, p_period, p_meetings, p_tts_chars, p_llm_tokens, p_interventions, now())
  on conflict (tenant_id, period) do update set
    meetings      = semeclaw_usage_v1.meetings      + excluded.meetings,
    tts_chars     = semeclaw_usage_v1.tts_chars     + excluded.tts_chars,
    llm_tokens    = semeclaw_usage_v1.llm_tokens    + excluded.llm_tokens,
    interventions = semeclaw_usage_v1.interventions + excluded.interventions,
    updated_at    = now();
end;
$$;

-- --------------------------------------------------------------------------
-- Views — convenience for the admin dashboard
-- --------------------------------------------------------------------------
create or replace view semeclaw_tenant_usage_v1 as
select
  t.id            as tenant_id,
  t.name          as tenant_name,
  t.plan          as plan,
  t.status        as status,
  coalesce(u.period, to_char(now(), 'YYYY-MM')) as period,
  coalesce(u.meetings,      0) as meetings,
  coalesce(u.tts_chars,     0) as tts_chars,
  coalesce(u.llm_tokens,    0) as llm_tokens,
  coalesce(u.interventions, 0) as interventions,
  u.updated_at
from semeclaw_tenants_v1 t
left join semeclaw_usage_v1 u
  on u.tenant_id = t.id
 and u.period    = to_char(now(), 'YYYY-MM');

-- --------------------------------------------------------------------------
-- Roll-forward marker
-- --------------------------------------------------------------------------
comment on table semeclaw_tenants_v1
  is 'SemeClaw v1.0 — Multi-tenant identity + Stripe metadata. See war_room/v1/tenants.py.';
