-- Atomic, replay-safe billing and paid draft generation for AdClaw.
-- Applied after 2026_04_23_adclaw_credits.sql.

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

create or replace function adclaw_grant_subscription_invoice_credits(
  p_invoice_id    text,
  p_advertiser_id uuid,
  p_tier          text,
  p_months        int
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
     set wallet_credits = wallet_credits + v_total
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

revoke all on function adclaw_grant_subscription_invoice_credits(text, uuid, text, int) from public;
revoke all on function adclaw_generate_card_once(uuid, uuid, uuid, int, jsonb) from public;
grant execute on function adclaw_grant_subscription_invoice_credits(text, uuid, text, int) to service_role;
grant execute on function adclaw_generate_card_once(uuid, uuid, uuid, int, jsonb) to service_role;
