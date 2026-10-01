"""Run every behavioural check and report which tokens actually appeared.

Not a gate itself - the gates run each script individually so a failure names
the capability that broke. This is for working quickly while building.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXPECT = re.compile(r"^\s*EXPECT:\s*(\S+)", re.M)
# [a-z0-9_] not [a-z_]: "t41_e2e_multistep" has a digit in the middle, and a
# name-only pattern silently captured "t41_e" and then matched no file at all,
# so those checks reported "0 of 0" instead of failing.
CHECK = re.compile(r"^\s*CHECK:.*?(t\d+_[a-z0-9_]+)\.py", re.M)


def wanted() -> dict[str, str]:
    """Read the token each script must print straight from the ledger, so this
    runner cannot disagree with the gates about what success means."""
    ledger = (HERE.parent.parent / "GATES.md").read_text(encoding="utf-8")
    names = CHECK.findall(ledger)
    tokens = EXPECT.findall(ledger)
    return dict(zip(names, tokens))


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    expect = wanted()
    scripts = sorted(p for p in HERE.glob("t*.py") if p.stem in expect)
    if only:
        scripts = [p for p in scripts if only in p.stem]

    passed, failed = [], []
    for s in scripts:
        token = expect[s.stem]
        t0 = time.perf_counter()
        r = subprocess.run([sys.executable, str(s)], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=600, cwd=str(HERE.parent.parent))
        dt = time.perf_counter() - t0
        got = token in (r.stdout or "")
        (passed if got else failed).append(s.stem)
        mark = "PASS" if got else "FAIL"
        print(f"  {mark}  {dt:6.1f}s  {s.stem}")
        if not got:
            tail = ((r.stderr or "") + (r.stdout or "")).strip().splitlines()
            for line in tail[-4:]:
                print(f"           {line[:150]}")

    print(f"\n{len(passed)} passed, {len(failed)} failed of {len(scripts)}")
    if failed:
        print("failing: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
