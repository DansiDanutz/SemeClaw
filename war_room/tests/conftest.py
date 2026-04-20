"""War Room test configuration.

pytest-asyncio is registered in the repo-root conftest.py so pytest 8+
does not fail collection on nested pytest_plugins declarations.
"""

import sys
from pathlib import Path

# Add war_room directory to path so tests can import local modules directly
sys.path.insert(0, str(Path(__file__).parent.parent))
