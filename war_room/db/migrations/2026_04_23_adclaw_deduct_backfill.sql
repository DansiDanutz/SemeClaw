-- Atomic credit deduction with balance check + one-time backfill for users
-- who were created before the 10-credit welcome grant existed.

-- 1) Deduct credits atomically. Returns {ok:bool, wallet_credits, error?}
create or replace function adclaw_deduct_credits(
  p_advertiser_id uuid,
  p_credits       int,
  p_description   text default 'Card generation'
) returns jsonb
  language plpgsql security definer as $$
declare
  v_balance int;
begin
  if p_credits <= 0 then
    return jsonb_build_object('ok', false, 'error', 'credits must be > 0');
  end if;
  select wallet_credits into v_balance
    from adclaw_advertisers where id = p_advertiser_id for update;
  if v_balance is null then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;
  if v_balance < p_credits then
    return jsonb_build_object('ok', false, 'error', 'insufficient credits',
                              'wallet_credits', v_balance, 'cost', p_credits);
  end if;
  update adclaw_advertisers
     set wallet_credits = wallet_credits - p_credits
   where id = p_advertiser_id;
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'spend', -p_credits, p_description);
  return jsonb_build_object('ok', true,
                            'wallet_credits', v_balance - p_credits,
                            'spent', p_credits);
end;
$$;

grant execute on function adclaw_deduct_credits(uuid, int, text) to service_role;

-- 2) One-time backfill: give the 10-credit welcome grant to existing users who
-- predate adclaw_ensure_wallet. Idempotent — only touches rows that have
-- wallet_credits=0 AND no transactions yet.
do $$
declare
  v_count int;
begin
  with targets as (
    select a.id
      from adclaw_advertisers a
     where coalesce(a.wallet_credits, 0) = 0
       and not exists (
         select 1 from adclaw_credit_transactions t where t.advertiser_id = a.id
       )
  ),
  updated as (
    update adclaw_advertisers a
       set wallet_credits = 10
      from targets t
     where a.id = t.id
     returning a.id
  )
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    select id, 'adjustment', 10, 'Welcome bonus backfill — 10 free credits'
      from updated;
  get diagnostics v_count = row_count;
  raise notice 'Backfilled % users with welcome credits', v_count;
end $$;
