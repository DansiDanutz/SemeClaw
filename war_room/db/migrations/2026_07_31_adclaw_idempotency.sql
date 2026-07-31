-- Atomic, replay-safe billing and paid draft generation for AdClaw.
-- Applied after the full ordered AdClaw schema sequence documented in AGENTS.md.

create table if not exists adclaw_stripe_credit_grants (
  invoice_id    text primary key,
  advertiser_id uuid not null references adclaw_advertisers(id) on delete cascade,
  tier          text not null check (tier in ('gold', 'diamond')),
  months        int not null check (months in (1, 12)),
  credits       int not null check (credits > 0),
  created_at    timestamptz not null default now()
);

create table if not exists adclaw_generated_drafts (
  request_id    uuid primary key,
  advertiser_id uuid not null references adclaw_advertisers(id) on delete cascade,
  project_id    uuid not null references adclaw_projects(id) on delete cascade,
  draft         jsonb not null,
  cost          int not null check (cost > 0),
  created_at    timestamptz not null default now()
);

alter table adclaw_stripe_credit_grants enable row level security;
alter table adclaw_generated_drafts enable row level security;
revoke all on adclaw_stripe_credit_grants from anon, authenticated;
revoke all on adclaw_generated_drafts from anon, authenticated;


alter table adclaw_advertisers
  add column if not exists subscription_checkout_reserved_until timestamptz,
  add column if not exists subscription_checkout_token uuid,
  add column if not exists subscription_checkout_price_id text,
  add column if not exists stripe_checkout_session_id text,
  add column if not exists stripe_checkout_url text;

create or replace function adclaw_reserve_subscription_checkout(
  p_advertiser_id uuid,
  p_price_id text
) returns jsonb
  language plpgsql
  security definer
  set search_path = public, pg_temp
as $reserve$
declare
  v_is_subscribed boolean;
  v_reserved_until timestamptz;
  v_token uuid;
  v_price_id text;
  v_session_id text;
  v_checkout_url text;
begin
  perform pg_advisory_xact_lock(
    hashtextextended('adclaw-subscription-checkout:' || p_advertiser_id::text, 0)
  );

  select
      is_subscribed,
      subscription_checkout_reserved_until,
      subscription_checkout_token,
      subscription_checkout_price_id,
      stripe_checkout_session_id,
      stripe_checkout_url
    into
      v_is_subscribed,
      v_reserved_until,
      v_token,
      v_price_id,
      v_session_id,
      v_checkout_url
    from adclaw_advertisers
   where id = p_advertiser_id
   for update;

  if not found then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;
  if nullif(trim(p_price_id), '') is null then
    return jsonb_build_object('ok', false, 'error', 'subscription price required');
  end if;
  if coalesce(v_is_subscribed, false) then
    return jsonb_build_object(
      'ok', false,
      'error', 'an active subscription must be changed or canceled before starting another checkout'
    );
  end if;
  if v_reserved_until is not null and v_reserved_until > now() then
    if v_price_id is distinct from p_price_id then
      return jsonb_build_object(
        'ok', false,
        'error', 'a checkout for a different subscription plan is already active',
        'reserved_price_id', v_price_id
      );
    end if;
    return jsonb_build_object(
      'ok', true,
      'already_reserved', true,
      'reserved_until', v_reserved_until,
      'reservation_token', v_token,
      'price_id', v_price_id,
      'session_id', v_session_id,
      'checkout_url', v_checkout_url
    );
  end if;

  v_reserved_until := now() + interval '24 hours';
  v_token := gen_random_uuid();
  update adclaw_advertisers
     set subscription_checkout_reserved_until = v_reserved_until,
         subscription_checkout_token = v_token,
         subscription_checkout_price_id = p_price_id,
         stripe_checkout_session_id = null,
         stripe_checkout_url = null
   where id = p_advertiser_id;

  return jsonb_build_object(
    'ok', true,
    'already_reserved', false,
    'reserved_until', v_reserved_until,
    'reservation_token', v_token,
    'price_id', p_price_id
  );
end;
$reserve$;

create or replace function adclaw_store_subscription_checkout(
  p_advertiser_id uuid,
  p_reservation_token uuid,
  p_session_id text,
  p_checkout_url text
) returns jsonb
  language plpgsql
  security definer
  set search_path = public, pg_temp
as $store$
begin
  if nullif(trim(p_session_id), '') is null
     or nullif(trim(p_checkout_url), '') is null then
    return jsonb_build_object('ok', false, 'error', 'checkout identity required');
  end if;

  perform pg_advisory_xact_lock(
    hashtextextended('adclaw-subscription-checkout:' || p_advertiser_id::text, 0)
  );

  update adclaw_advertisers
     set stripe_checkout_session_id = p_session_id,
         stripe_checkout_url = p_checkout_url
   where id = p_advertiser_id
     and subscription_checkout_token = p_reservation_token
     and subscription_checkout_reserved_until > now();

  if not found then
    return jsonb_build_object('ok', false, 'error', 'checkout reservation expired');
  end if;

  return jsonb_build_object(
    'ok', true,
    'session_id', p_session_id,
    'checkout_url', p_checkout_url
  );
end;
$store$;

create or replace function adclaw_grant_subscription_invoice_credits(
  p_invoice_id    text,
  p_advertiser_id uuid,
  p_tier          text,
  p_months        int,
  p_expires_at    timestamptz
) returns jsonb
  language plpgsql
  security definer
  set search_path = public, pg_temp
as $$
declare
  v_monthly int;
  v_total   int;
begin
  if nullif(trim(p_invoice_id), '') is null then
    return jsonb_build_object('ok', false, 'error', 'invoice id required');
  end if;
  if p_tier = 'gold' then
    v_monthly := 300;
  elsif p_tier = 'diamond' then
    v_monthly := 750;
  else
    return jsonb_build_object('ok', false, 'error', 'invalid tier');
  end if;
  if p_months not in (1, 12) then
    return jsonb_build_object('ok', false, 'error', 'invalid benefit period');
  end if;

  perform pg_advisory_xact_lock(hashtextextended('adclaw-invoice:' || p_invoice_id, 0));

  if exists (select 1 from adclaw_stripe_credit_grants where invoice_id = p_invoice_id) then
    return jsonb_build_object('ok', true, 'already_processed', true, 'credits', 0);
  end if;

  v_total := v_monthly * p_months;
  update adclaw_advertisers
     set wallet_credits = wallet_credits + v_total,
         tier = p_tier,
         is_subscribed = true,
         sub_expires_at = coalesce(p_expires_at, sub_expires_at),
         subscription_checkout_reserved_until = null,
         subscription_checkout_token = null,
         subscription_checkout_price_id = null,
         stripe_checkout_session_id = null,
         stripe_checkout_url = null
   where id = p_advertiser_id;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;

  insert into adclaw_stripe_credit_grants
    (invoice_id, advertiser_id, tier, months, credits)
  values
    (p_invoice_id, p_advertiser_id, p_tier, p_months, v_total);

  insert into adclaw_credit_transactions
    (advertiser_id, type, credits, description)
  values
    (p_advertiser_id, 'subscription', v_total,
     format('%s subscription · %s prepaid month%s · invoice %s',
            initcap(p_tier), p_months, case when p_months = 1 then '' else 's' end, p_invoice_id));

  return jsonb_build_object(
    'ok', true,
    'already_processed', false,
    'credits', v_total,
    'months', p_months,
    'tier', p_tier
  );
end;
$$;

create or replace function adclaw_generate_card_once(
  p_request_id    uuid,
  p_advertiser_id uuid,
  p_project_id    uuid,
  p_cost          int,
  p_draft         jsonb
) returns jsonb
  language plpgsql
  security definer
  set search_path = public, pg_temp
as $$
declare
  v_existing adclaw_generated_drafts%rowtype;
  v_wallet   int;
begin
  if p_cost <= 0 or p_draft is null then
    return jsonb_build_object('ok', false, 'error', 'invalid generation payload');
  end if;

  perform pg_advisory_xact_lock(hashtextextended('adclaw-draft:' || p_request_id::text, 0));

  select * into v_existing
    from adclaw_generated_drafts
   where request_id = p_request_id;

  if found then
    if v_existing.advertiser_id <> p_advertiser_id or v_existing.project_id <> p_project_id then
      return jsonb_build_object('ok', false, 'error', 'idempotency key scope mismatch');
    end if;
    return jsonb_build_object(
      'ok', true,
      'already_processed', true,
      'draft', v_existing.draft,
      'cost', v_existing.cost
    );
  end if;

  if not exists (
    select 1 from adclaw_projects
     where id = p_project_id and advertiser_id = p_advertiser_id
  ) then
    return jsonb_build_object('ok', false, 'error', 'project not found');
  end if;

  select wallet_credits into v_wallet
    from adclaw_advertisers
   where id = p_advertiser_id
   for update;

  if v_wallet is null then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;
  if v_wallet < p_cost then
    return jsonb_build_object(
      'ok', false,
      'error', 'insufficient credits',
      'wallet_credits', v_wallet,
      'cost', p_cost
    );
  end if;

  update adclaw_advertisers
     set wallet_credits = wallet_credits - p_cost
   where id = p_advertiser_id;

  insert into adclaw_generated_drafts
    (request_id, advertiser_id, project_id, draft, cost)
  values
    (p_request_id, p_advertiser_id, p_project_id, p_draft, p_cost);

  insert into adclaw_credit_transactions
    (advertiser_id, type, credits, description)
  values
    (p_advertiser_id, 'spend', -p_cost,
     format('Generated ad card for project %s · request %s', p_project_id, p_request_id));

  return jsonb_build_object(
    'ok', true,
    'already_processed', false,
    'draft', p_draft,
    'cost', p_cost,
    'wallet_credits', v_wallet - p_cost
  );
end;
$$;

-- Remove default PUBLIC execution from legacy credit-minting entry points.
-- The backend service role is the only caller allowed to create paid credits.
revoke all on function adclaw_topup_credits(uuid, int, text) from public, anon, authenticated;
revoke all on function adclaw_grant_subscription_credits(uuid, text) from public, anon, authenticated;
revoke all on function adclaw_record_topup(uuid, int, text) from public, anon, authenticated;
grant execute on function adclaw_topup_credits(uuid, int, text) to service_role;
grant execute on function adclaw_grant_subscription_credits(uuid, text) to service_role;
grant execute on function adclaw_record_topup(uuid, int, text) to service_role;

revoke all on function adclaw_reserve_subscription_checkout(uuid, text) from public, anon, authenticated;
grant execute on function adclaw_reserve_subscription_checkout(uuid, text) to service_role;
revoke all on function adclaw_store_subscription_checkout(uuid, uuid, text, text) from public, anon, authenticated;
grant execute on function adclaw_store_subscription_checkout(uuid, uuid, text, text) to service_role;
revoke all on function adclaw_grant_subscription_invoice_credits(text, uuid, text, int, timestamptz) from public;
revoke all on function adclaw_generate_card_once(uuid, uuid, uuid, int, jsonb) from public;
grant execute on function adclaw_grant_subscription_invoice_credits(text, uuid, text, int, timestamptz) to service_role;
grant execute on function adclaw_generate_card_once(uuid, uuid, uuid, int, jsonb) to service_role;
