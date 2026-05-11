"""SemeClaw v1.0 enhancements.

A self-contained package layered on top of the existing war_room dashboard.
See ``war_room/v1/routes.py`` for the wire-up entry point.
"""

V1_VERSION = "1.0.0-rc1"


def _maybe_activate_local_tasks() -> None:
    """Swap the Supabase-backed tasks store for an in-memory one when
    Supabase env vars are missing. Lets /tasks + intervention loop work
    in zero-key demos."""
    try:
        from war_room.v1 import local_tasks as _lt

        if not _lt.should_activate():
            return
        from war_room.tasks import _db as _tasks_db

        _lt.patch_into(_tasks_db)
    except Exception:  # pragma: no cover - best-effort activation
        pass


_maybe_activate_local_tasks()


from war_room.v1.routes import register_v1  # noqa: E402

__all__ = ["register_v1", "V1_VERSION"]
