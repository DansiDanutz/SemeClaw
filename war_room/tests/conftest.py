"""
Test configuration for War Room tests.
Sets up async event loop for pytest-asyncio.
"""
import pytest

# Configure asyncio mode for all async tests
pytest_plugins = ["pytest_asyncio"]
