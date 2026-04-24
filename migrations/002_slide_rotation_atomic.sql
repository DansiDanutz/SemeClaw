-- 002_slide_rotation_atomic.sql
-- Atomic slide picker for /api/slides/next to close the concurrent-serve race.
--
-- Why:
--   The Python side runs:
--     SELECT ... FROM adclaw_slides
--      WHERE status = 'enabled'
--      ORDER BY last_served_at ASC NULLS FIRST
--      LIMIT 50
--   Then picks the top `count` rows and stamps last_served_at separately.
--   Two concurrent requests arriving within the same window read the same
--   "oldest" rows and serve them to both callers — so round-robin fairness
--   silently degrades under load.
--
-- What this does:
--   `adclaw_pick_slides(n)` selects up to N eligible rows with `FOR UPDATE
--   SKIP LOCKED`, stamps `last_served_at = now()` atomically, and returns
--   them in the same transaction. Concurrent callers skip rows another
--   session has locked and select different slides.
--
-- Apply:
--   In Supabase SQL Editor on the ad project, paste this file and run.
--   Then the Python side can switch to `SELECT * FROM adclaw_pick_slides(5)`.
--
-- Rollback:
--   DROP FUNCTION IF EXISTS public.adclaw_pick_slides(int);

CREATE OR REPLACE FUNCTION public.adclaw_pick_slides(p_count int)
RETURNS SETOF public.adclaw_slides
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  n int := GREATEST(1, LEAST(COALESCE(p_count, 5), 50));
BEGIN
  RETURN QUERY
  WITH picked AS (
    SELECT id
    FROM public.adclaw_slides
    WHERE status = 'enabled'
    ORDER BY last_served_at ASC NULLS FIRST
    LIMIT n
    FOR UPDATE SKIP LOCKED
  ),
  stamped AS (
    UPDATE public.adclaw_slides
       SET last_served_at = now()
     WHERE id IN (SELECT id FROM picked)
     RETURNING *
  )
  SELECT * FROM stamped;
END;
$$;

COMMENT ON FUNCTION public.adclaw_pick_slides(int) IS
  'Atomically pick up to N eligible slides and stamp last_served_at. Called
   from the War Room AdClaw server to prevent the concurrent-serve race.';

-- Grant RPC access to service role (adjust to your Supabase role setup)
GRANT EXECUTE ON FUNCTION public.adclaw_pick_slides(int) TO service_role;
