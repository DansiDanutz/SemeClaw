"""Tiny terminal-UI helpers — colors, boxes, status icons. No third-party deps."""
from __future__ import annotations

import os
import sys

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


class _C:
    R   = "" if _NO_COLOR else "\033[31m"
    G   = "" if _NO_COLOR else "\033[32m"
    Y   = "" if _NO_COLOR else "\033[33m"
    B   = "" if _NO_COLOR else "\033[34m"
    C   = "" if _NO_COLOR else "\033[36m"
    M   = "" if _NO_COLOR else "\033[35m"
    DIM = "" if _NO_COLOR else "\033[2m"
    BLD = "" if _NO_COLOR else "\033[1m"
    OFF = "" if _NO_COLOR else "\033[0m"


OK   = f"{_C.G}[OK]{_C.OFF}"
MISS = f"{_C.Y}[--]{_C.OFF}"
ERR  = f"{_C.R}[!!]{_C.OFF}"
INFO = f"{_C.C}[..]{_C.OFF}"


def banner(title: str, sub: str = "") -> None:
    bar = "=" * 68
    print(f"\n{_C.C}{bar}{_C.OFF}")
    print(f"  {_C.BLD}{title}{_C.OFF}")
    if sub:
        print(f"  {_C.DIM}{sub}{_C.OFF}")
    print(f"{_C.C}{bar}{_C.OFF}\n")


def section(label: str) -> None:
    print(f"\n{_C.BLD}{label}{_C.OFF}")
    print(f"{_C.DIM}{'-' * len(label)}{_C.OFF}")


def row(icon: str, label: str, value: str = "") -> None:
    if value:
        print(f"  {icon}  {label:<32} {_C.DIM}{value}{_C.OFF}")
    else:
        print(f"  {icon}  {label}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{_C.C}?{_C.OFF} {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return ans or default


def confirm(prompt: str, default: bool = True) -> bool:
    yn = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({yn})").lower()
    if not ans:
        return default
    return ans in ("y", "yes")


def hint(text: str) -> None:
    print(f"  {_C.DIM}> {text}{_C.OFF}")


def success(text: str) -> None:
    print(f"\n{_C.G}{_C.BLD}OK{_C.OFF}  {text}\n")


def fail(text: str) -> None:
    print(f"\n{_C.R}{_C.BLD}FAIL{_C.OFF}  {text}\n")
