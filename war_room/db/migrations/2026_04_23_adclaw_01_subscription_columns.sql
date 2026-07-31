-- Idempotent advertiser subscription columns required by tier and billing migrations.
-- Run after 2026_04_23_adclaw_00_base.sql and before adclaw_tier.sql.

alter table adclaw_advertisers
  add column if not exists stripe_customer_id text,
  add column if not exists is_subscribed boolean not null default false,
  add column if not exists sub_expires_at timestamptz;

-- Preserve customers created by the original base schema.
update adclaw_advertisers
   set stripe_customer_id = stripe_customer
 where stripe_customer_id is null
   and nullif(trim(stripe_customer), '') is not null;

create unique index if not exists adclaw_advertisers_stripe_customer_id_idx
  on adclaw_advertisers(stripe_customer_id)
  where stripe_customer_id is not null;
