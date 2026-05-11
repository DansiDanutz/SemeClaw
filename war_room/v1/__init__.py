"""SemeClaw v1.0 enhancements.

A self-contained package layered on top of the existing war_room dashboard.
See ``war_room/v1/routes.py`` for the wire-up entry point.
"""

V1_VERSION = "1.0.0-rc1"

from war_room.v1.routes import register_v1  # noqa: E402

__all__ = ["register_v1", "V1_VERSION"]
