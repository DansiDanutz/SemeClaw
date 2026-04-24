-- ════════════════════════════════════════════════════════════════════════
-- SemeClaw — task ingest, dialog generation, intervention loop
-- ════════════════════════════════════════════════════════════════════════
-- Read-only foundation (Phase A). Bidirectional write-back to source systems
-- is added in Phase B.

create extension if not exists "pgcrypto";

-- ── tasks ingested from external adapters or local files ────────────────
create table if not exists semeclaw_tasks (
  id              uuid primary key default gen_random_uuid(),
  source          text not null,                       -- paperclip | moltica | local | claude_code
  source_id       text,                                -- the id in the source system
  tenant_id       text default 'default',
  title           text not null,
  description     text,
  status          text not null default 'open',        -- open | in_progress | needs_review | done | archived
  priority        int  not null default 50,            -- 0-100, higher = more important
  assigned_agents text[] not null default '{}',        -- registry agent ids
  meta            jsonb not null default '{}'::jsonb,  -- raw payload from the source
  ingested_at     timestamptz not null default now(),
  last_seen_at    timestamptz not null default now(),
  archived_at     timestamptz,
  unique (source, source_id, tenant_id)
);
create index if not exists semeclaw_tasks_tenant_idx     on semeclaw_tasks(tenant_id, status);
create index if not exists semeclaw_tasks_ingest_idx     on semeclaw_tasks(ingested_at desc);
create index if not exists semeclaw_tasks_source_idx     on semeclaw_tasks(source, source_id);

-- ── generated dialogs (versioned per task) ───────────────────────────────
create table if not exists semeclaw_dialogs (
  id              uuid primary key default gen_random_uuid(),
  task_id         uuid not null references semeclaw_tasks(id) on delete cascade,
  version         int  not null default 1,
  lines           jsonb not null,                      -- [{agent_id, role, text, ts}, ...]
  audio_url       text,                                -- TTS output (Phase B)
  created_at      timestamptz not null default now(),
  superseded_by   uuid references semeclaw_dialogs(id),
  unique (task_id, version)
);
create index if not exists semeclaw_dialogs_task_idx on semeclaw_dialogs(task_id, version desc);

-- ── interventions: user comments + agent replies, capped at 3 per dialog ─
create table if not exists semeclaw_interventions (
  id              uuid primary key default gen_random_uuid(),
  task_id         uuid not null references semeclaw_tasks(id) on delete cascade,
  dialog_id       uuid not null references semeclaw_dialogs(id) on delete cascade,
  turn_index      int  not null,                       -- 1 | 2 | 3
  user_comment    text not null,
  agent_replies   jsonb not null default '[]'::jsonb,  -- [{agent_id, text, ts}, ...]
  orchestrator_decision jsonb,                         -- only set on the 3rd intervention
  created_at      timestamptz not null default now(),
  unique (dialog_id, turn_index)
);
create index if not exists semeclaw_interv_task_idx on semeclaw_interventions(task_id, created_at desc);

-- ── retention: 100 active tasks per tenant ───────────────────────────────
-- Soft cap. Old tasks are auto-archived (not deleted) so dialog history survives.
create or replace function semeclaw_enforce_retention(
  p_tenant_id text default 'default',
  p_cap       int  default 100
) returns jsonb
  language plpgsql security definer as $$
declare
  v_active_count int;
  v_archived_count int := 0;
begin
  select count(*) into v_active_count
    from semeclaw_tasks
   where tenant_id = p_tenant_id
     and status != 'archived';

  if v_active_count <= p_cap then
    return jsonb_build_object('ok', true, 'active', v_active_count, 'archived_now', 0);
  end if;

  with victims as (
    select id from semeclaw_tasks
     where tenant_id = p_tenant_id
       and status != 'archived'
     order by
       case status when 'done' then 0 else 1 end,    -- prefer archiving completed
       last_seen_at asc                              -- then oldest
     limit (v_active_count - p_cap)
  )
  update semeclaw_tasks
     set status = 'archived', archived_at = now()
   where id in (select id from victims);

  get diagnostics v_archived_count = row_count;
  return jsonb_build_object('ok', true,
                            'active', v_active_count - v_archived_count,
                            'archived_now', v_archived_count,
                            'cap', p_cap);
end;
$$;

create or replace function semeclaw_quota(p_tenant_id text default 'default')
  returns jsonb
  language sql stable as $$
    with counts as (
      select count(*) filter (where status != 'archived') as active,
             count(*) filter (where status = 'archived') as archived,
             count(*) as total
        from semeclaw_tasks
       where tenant_id = p_tenant_id
    )
    select jsonb_build_object(
      'tenant_id', p_tenant_id,
      'cap', 100,
      'active', active,
      'archived', archived,
      'total', total,
      'available', greatest(0, 100 - active),
      'over_cap', greatest(0, active - 100)
    )
    from counts;
$$;

-- ── upsert helper used by ingestor (idempotent on source+source_id) ──────
create or replace function semeclaw_upsert_task(
  p_source          text,
  p_source_id       text,
  p_tenant_id       text,
  p_title           text,
  p_description     text,
  p_status          text,
  p_assigned_agents text[],
  p_meta            jsonb default '{}'::jsonb
) returns uuid
  language plpgsql security definer as $$
declare
  v_id uuid;
begin
  insert into semeclaw_tasks (source, source_id, tenant_id, title, description,
                              status, assigned_agents, meta, ingested_at, last_seen_at)
    values (p_source, p_source_id, coalesce(p_tenant_id, 'default'), p_title,
            p_description, coalesce(p_status, 'open'),
            coalesce(p_assigned_agents, '{}'), coalesce(p_meta, '{}'::jsonb),
            now(), now())
  on conflict (source, source_id, tenant_id) do update
    set title           = excluded.title,
        description     = excluded.description,
        status          = excluded.status,
        assigned_agents = excluded.assigned_agents,
        meta            = excluded.meta,
        last_seen_at    = now()
  returning id into v_id;
  return v_id;
end;
$$;

grant execute on function semeclaw_enforce_retention(text, int) to service_role;
grant execute on function semeclaw_quota(text)                  to anon, authenticated, service_role;
grant execute on function semeclaw_upsert_task(text, text, text, text, text, text, text[], jsonb)
                                                                to service_role;

-- Sanity probe
select 'semeclaw_tasks' as table_name, count(*) as columns
  from information_schema.columns where table_name = 'semeclaw_tasks'
union all
select 'semeclaw_dialogs',       count(*) from information_schema.columns where table_name = 'semeclaw_dialogs'
union all
select 'semeclaw_interventions', count(*) from information_schema.columns where table_name = 'semeclaw_interventions';
