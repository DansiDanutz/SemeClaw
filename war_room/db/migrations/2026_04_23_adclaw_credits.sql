-- Credits system: signup grant, daily login bonus, subscription top-up, one-time top-up,
-- active-card cap enforcement. Matches spec:
--   Free    : 10 on signup, +5/day login if any active project, max 1 active card.
--   Gold    : +300 (250+50) per paid invoice, +5/day login, max 3 active cards.
--   Diamond : +750 (500+250) per paid invoice, +5/day PER active project, max 5 active cards.

-- ───────────────────────────────────────────────────────────────────────
-- 1) Column for idempotent daily-bonus tracking
-- ───────────────────────────────────────────────────────────────────────
alter table adclaw_advertisers
  add column if not exists last_daily_bonus_at timestamptz;

-- ───────────────────────────────────────────────────────────────────────
-- 2) Signup: ensure row exists with 10 credits and record the grant.
-- ───────────────────────────────────────────────────────────────────────
create or replace function adclaw_ensure_wallet(
  p_advertiser_id uuid,
  p_email         text default ''
) returns jsonb
  language plpgsql security definer as $$
declare
  v_credits int;
begin
  insert into adclaw_advertisers (id, email, wallet_credits, is_subscribed, tier)
    values (p_advertiser_id, p_email, 10, false, 'free')
  on conflict (id) do update
    set email = coalesce(nullif(excluded.email, ''), adclaw_advertisers.email)
  returning wallet_credits into v_credits;

  -- Only log the grant transaction if this is a brand-new row (wallet == 10
  -- AND no prior transactions exist for this advertiser).
  if v_credits = 10 and not exists (
    select 1 from adclaw_credit_transactions where advertiser_id = p_advertiser_id
  ) then
    insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
      values (p_advertiser_id, 'adjustment', 10, 'Welcome bonus — 10 free credits');
  end if;

  return jsonb_build_object('ok', true, 'wallet_credits', v_credits);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────
-- 3) Daily login bonus. Idempotent — only fires once per UTC day.
-- Free/Gold : +5 if user has at least one active project
-- Diamond   : +5 per active project (cap the count for sanity at 20)
-- ───────────────────────────────────────────────────────────────────────
create or replace function adclaw_daily_login_bonus(p_advertiser_id uuid)
  returns jsonb
  language plpgsql security definer as $$
declare
  v_tier          text;
  v_last          timestamptz;
  v_active_count  int;
  v_grant         int := 0;
begin
  select tier, last_daily_bonus_at
    into v_tier, v_last
    from adclaw_advertisers
   where id = p_advertiser_id;

  if v_tier is null then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;

  -- Already granted today?
  if v_last is not null and (v_last at time zone 'UTC')::date = (now() at time zone 'UTC')::date then
    return jsonb_build_object('ok', true, 'granted', 0, 'reason', 'already granted today');
  end if;

  -- Count user's active projects
  select count(*) into v_active_count
    from adclaw_projects
   where advertiser_id = p_advertiser_id
     and coalesce(status, 'active') = 'active';

  if v_active_count = 0 then
    return jsonb_build_object('ok', true, 'granted', 0, 'reason', 'no active project');
  end if;

  if v_tier = 'diamond' then
    v_grant := 5 * least(v_active_count, 20);
  else
    v_grant := 5;
  end if;

  update adclaw_advertisers
     set wallet_credits = wallet_credits + v_grant,
         last_daily_bonus_at = now()
   where id = p_advertiser_id;

  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'adjustment', v_grant,
            format('Daily login bonus (%s · %s active project%s)',
                   v_tier, v_active_count, case when v_active_count = 1 then '' else 's' end));

  return jsonb_build_object('ok', true, 'granted', v_grant, 'tier', v_tier,
                            'active_projects', v_active_count);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────
-- 4) Subscription credit grant. Called from the Stripe webhook on invoice.paid.
-- Gold    : 250 base + 50 bonus  (20%)
-- Diamond : 500 base + 250 bonus (50%)
-- ───────────────────────────────────────────────────────────────────────
create or replace function adclaw_grant_subscription_credits(
  p_advertiser_id uuid,
  p_tier          text
) returns jsonb
  language plpgsql security definer as $$
declare
  v_total int;
  v_base  int;
  v_bonus int;
  v_label text;
begin
  if p_tier = 'gold' then
    v_base := 250; v_bonus := 50;  v_label := 'Gold subscription ($25)';
  elsif p_tier = 'diamond' then
    v_base := 500; v_bonus := 250; v_label := 'Diamond subscription ($50)';
  else
    return jsonb_build_object('ok', false, 'error', 'invalid tier');
  end if;
  v_total := v_base + v_bonus;

  update adclaw_advertisers
     set wallet_credits = wallet_credits + v_total
   where id = p_advertiser_id;

  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'subscription', v_total,
            format('%s — %s base + %s bonus', v_label, v_base, v_bonus));

  return jsonb_build_object('ok', true, 'credits', v_total, 'base', v_base, 'bonus', v_bonus);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────
-- 5) One-time top-up. Called from Stripe webhook on checkout.session.completed
-- for mode=payment sessions. $1 = 10 credits.
-- ───────────────────────────────────────────────────────────────────────
create or replace function adclaw_record_topup(
  p_advertiser_id uuid,
  p_credits       int,
  p_stripe_id     text default null
) returns jsonb
  language plpgsql security definer as $$
begin
  if p_credits <= 0 then
    return jsonb_build_object('ok', false, 'error', 'credits must be > 0');
  end if;
  update adclaw_advertisers
     set wallet_credits = wallet_credits + p_credits
   where id = p_advertiser_id;
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'purchase', p_credits,
            format('Top-up · %s credits%s', p_credits,
                   case when p_stripe_id is null then '' else ' · ' || p_stripe_id end));
  return jsonb_build_object('ok', true, 'credits_added', p_credits);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────
-- 6) Active-card cap enforcement (1/3/5). Returns jsonb so callers can show
-- a specific error. Counts slides where status='active' for this advertiser
-- across all their campaigns.
-- ───────────────────────────────────────────────────────────────────────
create or replace function adclaw_active_card_count(p_advertiser_id uuid)
  returns int
  language sql stable as $$
    -- Counts cards in the serving pool (status='enabled'), which is what the
    -- meeting room actually fetches via adclaw/server.py:get_next_slide.
    -- 'active' in the UI means "currently in rotation" = same as enabled here.
    select count(*)::int
      from adclaw_slides s
      join adclaw_campaigns c on c.id = s.campaign_id
     where c.advertiser_id = p_advertiser_id
       and s.status in ('enabled', 'active');
$$;

create or replace function adclaw_can_activate_slide(p_advertiser_id uuid)
  returns jsonb
  language plpgsql stable as $$
declare
  v_tier text;
  v_cap  int;
  v_now  int;
begin
  select tier into v_tier from adclaw_advertisers where id = p_advertiser_id;
  v_cap := adclaw_tier_cap(coalesce(v_tier, 'free'));
  v_now := adclaw_active_card_count(p_advertiser_id);
  if v_now >= v_cap then
    return jsonb_build_object('ok', false, 'tier', v_tier, 'cap', v_cap,
                              'active', v_now,
                              'error', format('Tier %s allows max %s active card%s',
                                               v_tier, v_cap, case when v_cap=1 then '' else 's' end));
  end if;
  return jsonb_build_object('ok', true, 'tier', v_tier, 'cap', v_cap, 'active', v_now);
end;
$$;

-- ───────────────────────────────────────────────────────────────────────
-- 7) Grants
-- ───────────────────────────────────────────────────────────────────────
grant execute on function adclaw_ensure_wallet(uuid, text)                 to service_role;
grant execute on function adclaw_daily_login_bonus(uuid)                   to authenticated, service_role;
grant execute on function adclaw_grant_subscription_credits(uuid, text)    to service_role;
grant execute on function adclaw_record_topup(uuid, int, text)             to service_role;
grant execute on function adclaw_active_card_count(uuid)                   to authenticated, service_role;
grant execute on function adclaw_can_activate_slide(uuid)                  to authenticated, service_role;
