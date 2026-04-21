"""
Tests for scripts/ utilities.
"""

import tempfile
from pathlib import Path


class TestBumpVersion:
    def test_bump_patch(self, tmp_path: Path, monkeypatch):
        # Create temp files
        pyproject = tmp_path / "pyproject.toml"
        server_py = tmp_path / "server.py"
        readme = tmp_path / "README.md"

        pyproject.write_text('[project]\nversion = "0.7.0"\n')
        server_py.write_text('APP_VERSION = "0.7.0"\n')
        readme.write_text("version-0.7.0-10b981\n")

        # Import and patch paths
        import scripts.bump_version as bv
        monkeypatch.setattr(bv, "PYPROJECT", pyproject)
        monkeypatch.setattr(bv, "SERVER_PY", server_py)
        monkeypatch.setattr(bv, "README_MD", readme)

        assert bv.read_pyproject_version() == "0.7.0"
        bv.write_pyproject_version("0.7.1")
        bv.write_server_version("0.7.1")
        bv.write_readme_version("0.7.1")

        assert bv.read_pyproject_version() == "0.7.1"
        assert 'version = "0.7.1"' in pyproject.read_text()
        assert 'APP_VERSION = "0.7.1"' in server_py.read_text()
        assert "version-0.7.1-10b981" in readme.read_text()

    def test_bump_explicit(self):
        import scripts.bump_version as bv
        assert bv.bump("1.2.3", "patch") == "1.2.4"
        assert bv.bump("1.2.3", "minor") == "1.3.0"
        assert bv.bump("1.2.3", "major") == "2.0.0"
