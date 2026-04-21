"""
Bump SemeClaw version in pyproject.toml and war_room/dashboard/server.py.

Usage:
    python scripts/bump_version.py patch   # 0.7.0 -> 0.7.1
    python scripts/bump_version.py minor   # 0.7.0 -> 0.8.0
    python scripts/bump_version.py major   # 0.7.0 -> 1.0.0
    python scripts/bump_version.py 0.8.2   # set explicit version
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PYPROJECT = ROOT / "pyproject.toml"
SERVER_PY = ROOT / "war_room" / "dashboard" / "server.py"
README_MD = ROOT / "README.md"


def _bump_part(parts: list[int], idx: int) -> list[int]:
    parts[idx] += 1
    for i in range(idx + 1, len(parts)):
        parts[i] = 0
    return parts


def bump(version: str, part: str) -> str:
    parts = list(map(int, version.split(".")))
    if part == "major":
        parts = _bump_part(parts, 0)
    elif part == "minor":
        parts = _bump_part(parts, 1)
    else:
        parts = _bump_part(parts, 2)
    return ".".join(map(str, parts))


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([\d.]+)"', text, re.M)
    if not m:
        raise RuntimeError("version not found in pyproject.toml")
    return m.group(1)


def write_pyproject_version(new_ver: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    text = re.sub(r'^(version\s*=\s*)"[\d.]+"', rf'\1"{new_ver}"', text, flags=re.M)
    PYPROJECT.write_text(text, encoding="utf-8")


def write_server_version(new_ver: str) -> None:
    text = SERVER_PY.read_text(encoding="utf-8")
    text = re.sub(r'^(APP_VERSION\s*=\s*)"[\d.]+"', rf'\1"{new_ver}"', text, flags=re.M)
    SERVER_PY.write_text(text, encoding="utf-8")


def write_readme_version(new_ver: str) -> None:
    text = README_MD.read_text(encoding="utf-8")
    text = re.sub(r'(version-)[\d.]+(-10b981)', rf'\g<1>{new_ver}\g<2>', text)
    README_MD.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bump SemeClaw version")
    parser.add_argument("part", choices=["patch", "minor", "major"], nargs="?")
    parser.add_argument("--set", dest="explicit", metavar="VERSION", help="Set explicit version (e.g. 0.8.2)")
    args = parser.parse_args()

    current = read_pyproject_version()

    if args.explicit:
        new_ver = args.explicit
        if not re.match(r"^\d+\.\d+\.\d+$", new_ver):
            print(f"Invalid version format: {new_ver}", file=sys.stderr)
            return 1
    elif args.part:
        new_ver = bump(current, args.part)
    else:
        parser.print_help()
        return 1

    print(f"Bumping {current} -> {new_ver}")
    write_pyproject_version(new_ver)
    write_server_version(new_ver)
    write_readme_version(new_ver)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
