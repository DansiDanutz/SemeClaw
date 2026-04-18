"""
sentinel/tripwire_cli.py — CLI tool for tripwire management.

Usage:
  python3.13 -m sentinel.tripwire_cli status
  python3.13 -m sentinel.tripwire_cli approve <sha_prefix>
  python3.13 -m sentinel.tripwire_cli baseline   — show current baseline
  python3.13 -m sentinel.tripwire_cli reset       — rebuild baseline from current files
  python3.13 -m sentinel.tripwire_cli vault       — list vault entries
"""
import json
import sys
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).parent.parent))


def cmd_status():
    from sentinel.tripwire import load_baseline, _snapshot, _expand_paths
    from sentinel.thresholds import WATCHED_PATHS_MAC
    paths    = _expand_paths(WATCHED_PATHS_MAC)
    baseline = load_baseline()
    current  = _snapshot(paths)

    if not baseline:
        print("⚠️  No baseline yet — run Sentinel once to establish it")
        return

    ok   = []
    bad  = []
    new_ = []
    for p, old_sha in baseline.items():
        new_sha = current.get(p)
        if new_sha is None:
            bad.append(("DELETED", p, old_sha, None))
        elif new_sha != old_sha:
            bad.append(("MODIFIED", p, old_sha, new_sha))
        else:
            ok.append(p)
    for p, new_sha in current.items():
        if p not in baseline:
            new_.append(("NEW", p, None, new_sha))

    print(f"\n{'='*60}")
    print(f"Tripwire Status — {len(ok)} clean / {len(bad)} drifted / {len(new_)} new")
    print(f"{'='*60}")
    for status, p, old, new in bad + new_:
        print(f"  [{status}] {Path(p).name}")
        print(f"          {old[:10] if old else 'N/A'} → {new[:10] if new else 'DELETED'}")
    if not bad and not new_:
        print("  ✅ All files match baseline")
    print()


def cmd_approve(sha_prefix: str):
    from sentinel.tripwire import approve, load_baseline, _snapshot, _expand_paths, save_baseline
    from sentinel.thresholds import WATCHED_PATHS_MAC
    paths    = _expand_paths(WATCHED_PATHS_MAC)
    baseline = load_baseline()
    current  = _snapshot(paths)
    approve(sha_prefix)
    # Update any baseline entry whose current sha starts with sha_prefix
    updated = []
    for p, new_sha in current.items():
        if new_sha.startswith(sha_prefix) and baseline.get(p) != new_sha:
            baseline[p] = new_sha
            updated.append(p)
    save_baseline(baseline)
    print(f"✅ Approved {sha_prefix} — updated {len(updated)} baseline entries:")
    for p in updated:
        print(f"   {Path(p).name}")


def cmd_baseline():
    from sentinel.tripwire import load_baseline
    bl = load_baseline()
    if not bl:
        print("No baseline established yet.")
        return
    print(f"\nBaseline ({len(bl)} files):")
    for p, sha in sorted(bl.items()):
        print(f"  {sha[:10]}  {Path(p).name}")
    print()


def cmd_reset():
    from sentinel.tripwire import _expand_paths, _snapshot, save_baseline
    from sentinel.thresholds import WATCHED_PATHS_MAC
    paths = _expand_paths(WATCHED_PATHS_MAC)
    snap  = _snapshot(paths)
    save_baseline(snap)
    print(f"✅ Baseline reset to current state ({len(snap)} files)")


def cmd_vault():
    from sentinel.thresholds import TRIPWIRE_VAULT_DIR
    vault = Path(TRIPWIRE_VAULT_DIR)
    if not vault.exists():
        print("Vault is empty.")
        return
    files = sorted(vault.glob("*.gz"), key=lambda f: f.stat().st_mtime, reverse=True)
    print(f"\nVault ({len(files)} entries):")
    for f in files[:20]:
        ts = f.stat().st_mtime
        from datetime import datetime
        dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        print(f"  {f.stem}  {dt}  {f.stat().st_size//1024}KB")
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "approve":
        if len(sys.argv) < 3:
            print("Usage: tripwire_cli approve <sha_prefix>")
            sys.exit(1)
        cmd_approve(sys.argv[2])
    elif cmd == "baseline":
        cmd_baseline()
    elif cmd == "reset":
        cmd_reset()
    elif cmd == "vault":
        cmd_vault()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
