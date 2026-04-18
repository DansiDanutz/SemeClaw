-- migrations/project_kpis_daily.sql
-- Daily KPI tracking per project
-- Run against: DLS_TEAM_SUPABASE (okgwzwdtuhhpoyxyprzg)

CREATE TABLE IF NOT EXISTS project_kpis_daily (
    day                DATE        NOT NULL,
    project            TEXT        NOT NULL,
    tasks_completed    INT         DEFAULT 0,
    tasks_failed       INT         DEFAULT 0,
    tasks_in_progress  INT         DEFAULT 0,
    tokens_spent       BIGINT      DEFAULT 0,
    cost_usd           NUMERIC(10,4) DEFAULT 0,
    new_commits        INT         DEFAULT 0,
    deploys            INT         DEFAULT 0,
    blockers           TEXT[]      DEFAULT '{}',
    evolved            BOOLEAN     DEFAULT false,
    owner_agent        TEXT,
    created_at         TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (day, project)
);

CREATE TABLE IF NOT EXISTS projects_state (
    project            TEXT        PRIMARY KEY,
    last_evolved_at    TIMESTAMPTZ,
    open_tasks         INT         DEFAULT 0,
    completed_today    INT         DEFAULT 0,
    next_milestone     TEXT,
    owner_agent        TEXT,
    health             TEXT        DEFAULT 'unknown',  -- active | stale | dead
    updated_at         TIMESTAMPTZ DEFAULT now()
);

-- Index for fast daily queries
CREATE INDEX IF NOT EXISTS idx_kpis_day       ON project_kpis_daily (day DESC);
CREATE INDEX IF NOT EXISTS idx_kpis_project   ON project_kpis_daily (project);

-- Seed known projects
INSERT INTO projects_state (project, owner_agent, health)
VALUES
    ('nervix',      'dexter', 'active'),
    ('zmartychat',  'sienna', 'active'),
    ('mywork',      'memo',   'active'),
    ('paperclip',   'memo',   'active'),
    ('semeclaw',    'david',  'active'),
    ('crawdbot',    'dexter', 'active')
ON CONFLICT (project) DO NOTHING;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS kpis_updated_at ON project_kpis_daily;
CREATE TRIGGER kpis_updated_at
    BEFORE UPDATE ON project_kpis_daily
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS projects_state_updated_at ON projects_state;
CREATE TRIGGER projects_state_updated_at
    BEFORE UPDATE ON projects_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
