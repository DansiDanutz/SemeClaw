-- Add explicit tier column + featured-projects management.
-- Apply via Supabase SQL editor or: psql $DATABASE_URL -f <this file>

-- 1) Tier column on advertisers
alter table adclaw_advertisers
  add column if not exists tier text not null default 'free'
    check (tier in ('free','gold','diamond'));

-- Backfill: existing is_subscribed users were the $50/mo Diamond tier.
update adclaw_advertisers
   set tier = 'diamond'
 where is_subscribed = true and tier = 'free';

-- 2) Cap per tier for active featured projects
create or replace function adclaw_tier_cap(p_tier text) returns int
  language sql immutable as $$
    select case p_tier
      when 'diamond' then 5
      when 'gold'    then 3
      else 1
    end;
$$;

-- 3) Replace the user's featured projects atomically.
-- p_project_ids: array of slide/project ids to feature. Length must be <= tier cap.
-- Free tier gets 0 spotlight slots regardless.
create or replace function adclaw_set_featured(
  p_advertiser_id uuid,
  p_project_ids   uuid[]
) returns jsonb
  language plpgsql security definer as $$
declare
  v_tier text;
  v_cap  int;
  v_n    int := coalesce(array_length(p_project_ids, 1), 0);
begin
  select tier into v_tier from adclaw_advertisers where id = p_advertiser_id;
  if v_tier is null then
    return jsonb_build_object('ok', false, 'error', 'advertiser not found');
  end if;
  if v_tier = 'free' then
    return jsonb_build_object('ok', false, 'error', 'spotlight requires Gold or Diamond');
  end if;
  v_cap := adclaw_tier_cap(v_tier);
  if v_n > v_cap then
    return jsonb_build_object('ok', false, 'error',
      format('tier %s allows max %s featured projects', v_tier, v_cap));
  end if;
  -- Clear this advertiser's current spotlight rows, then re-insert the chosen set.
  delete from adclaw_spotlight where user_id = p_advertiser_id;
  insert into adclaw_spotlight (user_id, project_id, featured_from, ttl_days)
  select p_advertiser_id, pid, now(),
         case when v_tier = 'diamond' then 31 else 7 end
    from unnest(p_project_ids) as pid;
  return jsonb_build_object('ok', true, 'featured', v_n, 'cap', v_cap);
end;
$$;

-- 4) Add project_id column to spotlight (so we can reference the user's existing
-- projects rather than re-entering name/url/logo each time).
alter table adclaw_spotlight
  add column if not exists project_id uuid references adclaw_projects(id) on delete cascade;

-- 5) Downgrade helper invoked by the Stripe webhook on invoice.payment_failed
-- or customer.subscription.deleted.
create or replace function adclaw_downgrade_to_free(p_advertiser_id uuid)
  returns void language sql security definer as $$
    update adclaw_advertisers
       set tier = 'free',
           is_subscribed = false,
           sub_expires_at = null
     where id = p_advertiser_id;
    delete from adclaw_spotlight where user_id = p_advertiser_id;
$$;
