-- ════════════════════════════════════════════════════════════════════════
-- ad-NOW revenue features — Special tab backing schema
-- (same block that was delivered in chat)
-- ════════════════════════════════════════════════════════════════════════

alter table adclaw_slides
  add column if not exists priority_boost   boolean default false,
  add column if not exists target_keywords  text[]  default '{}',
  add column if not exists click_count      int     default 0,
  add column if not exists last_clicked_at  timestamptz;

alter table adclaw_advertisers
  add column if not exists referral_code text unique,
  add column if not exists referred_by   uuid references adclaw_advertisers(id);

update adclaw_advertisers
   set referral_code = substr(md5(id::text || clock_timestamp()::text), 1, 8)
 where referral_code is null;

create or replace function adclaw_assign_referral_code() returns trigger
  language plpgsql as $$
begin
  if new.referral_code is null then
    new.referral_code := substr(md5(new.id::text || clock_timestamp()::text), 1, 8);
  end if;
  return new;
end;
$$;
drop trigger if exists adclaw_advertisers_referral_code on adclaw_advertisers;
create trigger adclaw_advertisers_referral_code
  before insert on adclaw_advertisers
  for each row execute function adclaw_assign_referral_code();

create or replace function adclaw_apply_referral(
  p_advertiser_id uuid,
  p_ref_code      text
) returns jsonb
  language plpgsql security definer as $$
declare
  v_referrer uuid;
  v_self     text;
begin
  if p_ref_code is null or length(trim(p_ref_code)) = 0 then
    return jsonb_build_object('ok', false, 'error', 'no code');
  end if;
  select referral_code into v_self from adclaw_advertisers where id = p_advertiser_id;
  if v_self = p_ref_code then
    return jsonb_build_object('ok', false, 'error', 'self referral');
  end if;
  select id into v_referrer from adclaw_advertisers where referral_code = p_ref_code;
  if v_referrer is null then
    return jsonb_build_object('ok', false, 'error', 'invalid code');
  end if;
  update adclaw_advertisers
     set referred_by = v_referrer
   where id = p_advertiser_id
     and referred_by is null;
  if not found then
    return jsonb_build_object('ok', false, 'error', 'already referred');
  end if;
  update adclaw_advertisers set wallet_credits = wallet_credits + 20 where id = v_referrer;
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (v_referrer, 'adjustment', 20,
            format('Referral bonus — referred advertiser %s', substr(p_advertiser_id::text,1,8)));
  update adclaw_advertisers set wallet_credits = wallet_credits + 10 where id = p_advertiser_id;
  insert into adclaw_credit_transactions (advertiser_id, type, credits, description)
    values (p_advertiser_id, 'adjustment', 10, 'Referral welcome — used code ' || p_ref_code);
  return jsonb_build_object('ok', true, 'referrer', v_referrer, 'granted_you', 10, 'granted_referrer', 20);
end;
$$;

create or replace function adclaw_record_click(
  p_slide_id uuid,
  p_ip_hash  text default ''
) returns jsonb
  language plpgsql security definer as $$
declare
  v_adv uuid;
begin
  select c.advertiser_id into v_adv
    from adclaw_slides s
    join adclaw_campaigns c on c.id = s.campaign_id
   where s.id = p_slide_id;
  if v_adv is null then
    return jsonb_build_object('ok', false, 'error', 'slide not found');
  end if;
  update adclaw_slides
     set click_count = click_count + 1,
         last_clicked_at = now()
   where id = p_slide_id;
  update adclaw_advertisers
     set wallet_credits = greatest(0, wallet_credits - 5)
   where id = v_adv and wallet_credits >= 5;
  if found then
    insert into adclaw_credit_transactions (advertiser_id, type, credits, description, slide_id)
      values (v_adv, 'spend', -5, 'Click through on ad card', p_slide_id);
  end if;
  return jsonb_build_object('ok', true, 'advertiser_id', v_adv);
end;
$$;

create or replace function adclaw_slide_analytics(p_advertiser_id uuid)
  returns table (
    slide_id    uuid,
    headline    text,
    impressions int,
    clicks      int,
    ctr_pct     numeric,
    priority    boolean
  )
  language sql stable as $$
    select s.id, s.headline,
           coalesce((select count(*)::int from adclaw_impressions i where i.slide_id = s.id), 0) as impressions,
           coalesce(s.click_count, 0) as clicks,
           case when coalesce((select count(*) from adclaw_impressions i where i.slide_id = s.id),0) = 0 then 0
                else round(100.0 * coalesce(s.click_count,0)::numeric /
                     (select count(*) from adclaw_impressions i where i.slide_id = s.id), 2) end as ctr_pct,
           coalesce(s.priority_boost, false)
      from adclaw_slides s
      join adclaw_campaigns c on c.id = s.campaign_id
     where c.advertiser_id = p_advertiser_id
     order by impressions desc;
$$;

grant execute on function adclaw_apply_referral(uuid, text)     to authenticated, service_role;
grant execute on function adclaw_record_click(uuid, text)       to anon, authenticated, service_role;
grant execute on function adclaw_slide_analytics(uuid)          to authenticated, service_role;
