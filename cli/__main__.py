"""`python -m cli` and `semeclaw` entry point."""
from __future__ import annotations

import sys

USAGE = """\
SemeClaw CLI

Usage:
  semeclaw setup       Interactive onboarding (API keys, smoke test, save env)
  semeclaw status      Checklist of what's configured + what's missing
  semeclaw agents      List all registered agents (core + adapters)
  semeclaw demo        Run the saved 4-agent live demo task
  semeclaw help        Show this message
"""


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    cmd = (args[0] if args else "help").lower()
    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd == "setup":
        from . import setup
        return setup.run()
    if cmd == "status":
        from . import status
        return status.run()
    if cmd == "agents":
        from . import agents
        return agents.run()
    if cmd == "demo":
        from . import demo
        return demo.run()
    print(f"Unknown command: {cmd}\n")
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
