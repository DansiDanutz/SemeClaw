"""Dashboard API routers.

Routers are extracted from the monolithic server.py incrementally.
Each router file contains a subset of related endpoints.

To add a new router:
  1. Create routes/<name>.py with an APIRouter instance
  2. Import and mount it in server.py
"""
