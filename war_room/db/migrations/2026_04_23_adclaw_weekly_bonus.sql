-- ════════════════════════════════════════════════════════════════════════
-- ad-NOW — Referral tree + weekly 10%-of-spend bonus, paid every Monday
-- ════════════════════════════════════════════════════════════════════════

-- 1) Referral tree: for each user you referred, return their email (short),
--    id prefix, and how many credits they *spent* in the last 7 days.
--    'spend' rows in adclaw_credit_transactions carry negative credits,
--    so we abs() them.
create or replace function adclaw_referral_tree(p_advertiser_id uuid)
  returns table (
    id         uuid,
    id_short   text,
    email      text,
    week_spent int,
    joined_at  timestamptz
  )
  language sql stable as $$
    select a.id,
           substr(a.id::text, 1, 8) as id_short,
           coalesce(a.email, '')    as email,
           coalesce((
             select abs(sum(t.credits))::int
               from adclaw_credit_transactions t
              where t.advertiser_id = a.id
                and t.type = 'spend'
                and t.created_at >= now() - interval '7 days'
           ), 0) as week_spent,
           a.created_at as joined_at
      from adclaw_advertisers a
     where a.referred_by = p_advertiser_id
     order by a.created_at desc;
$$;
grant execute on function adclaw_referral_tree(uuid) to authenticated, service_role;

-- 2) Pay weekly referral bonus (10% of each referred user's last-7d spend).
--    Idempotent within a 6-day window: we skip any referrer who already
--    received a bonus in the last 6 days (so Monday cron can retry safely).
create or replace function adclaw_pay_weekly_referral_bonus()
  returns jsonb
  language plpgsql security definer as $$
declare
  r          record;
  v_spend    int;
  v_bonus    int;
  v_paid_ct  int := 0;
  v_paid_cr  int := 0;
begin
  for r in
    select distinct referred_by as referrer
      from adclaw_advertisers
     where referred_by is not null
  loop
    -- Skip if we already paid this referrer in the last 6 days
    if exists (
      select 1 from adclaw_credit_transactions
       where advertiser_id = r.referrer
         and description like 'Weekly referral bonus%'
         and created_at >= now() - interval '6 days'
    ) then
      continue;
    end if;

    -- Sum abs(spend) from all referred users in the last 7 days
    select coalesce(abs(sum(t.credits))::int, 0)
      into v_spend
      from adclaw_credit_transactions t
      join adclaw_advertisers a on a.id = t.advertiser_id
     where a.referred_by = r.referrer
       and t.type = 'spend'
       and t.created_at >= now() - interval '7 days';

    v_bonus := floor(v_spend * 0.10)::int;
    if v_bonus <= 0 then
      continue;
    end if;

    update adclaw_advertisers
       set wallet_credits = wallet_credits + v_bonus
     where id = r.referrer;

    insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
      values (r.referrer, 'adjustment', v_bonus,
              format('Weekly referral bonus — 10%% of %s credits spent by referrals', v_spend));

    v_paid_ct := v_paid_ct + 1;
    v_paid_cr := v_paid_cr + v_bonus;
  end loop;

  return jsonb_build_object(
    'ok', true,
    'referrers_paid', v_paid_ct,
    'credits_paid',   v_paid_cr,
    'ran_at',         now()
  );
end;
$$;
grant execute on function adclaw_pay_weekly_referral_bonus() to service_role;

-- 3) Schedule with pg_cron for Monday 00:00 UTC.
--    If pg_cron isn't enabled, this block is a no-op (wrapped in DO).
do $$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    -- Unschedule any prior version (id-safe)
    perform cron.unschedule(jobid)
       from cron.job
      where jobname = 'adclaw_weekly_referral_bonus';
    perform cron.schedule(
      'adclaw_weekly_referral_bonus',
      '0 0 * * 1',  -- 00:00 UTC every Monday
      $cron$ select adclaw_pay_weekly_referral_bonus(); $cron$
    );
    raise notice 'Scheduled adclaw_weekly_referral_bonus for Monday 00:00 UTC';
  else
    raise notice 'pg_cron not installed — call adclaw_pay_weekly_referral_bonus() from a backend cron instead';
  end if;
end $$;
