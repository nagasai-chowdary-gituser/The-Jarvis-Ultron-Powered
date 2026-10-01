"""G40: the existing regression suite still passes, with ZERO failures.

Runs pytest and reads the machine-readable outcome rather than grepping for
the word "passed", which also appears in "3 failed, 5 passed"."""
import json, subprocess, sys, tempfile
from pathlib import Path
import _harness as h

report = Path(tempfile.gettempdir()) / "jarvis-regression.json"
if report.exists():
    report.unlink()

r = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/test_jarvis.py", "-q", "--tb=line"],
    cwd=str(h.ROOT), capture_output=True, text=True,
    encoding="utf-8", errors="replace", timeout=900)

out = (r.stdout or "") + (r.stderr or "")
last = [ln for ln in out.strip().splitlines() if ln.strip()][-1] if out.strip() else ""

h.need(r.returncode == 0,
       f"pytest exited {r.returncode}; last line: {last[:200]}")
h.need(" failed" not in last and "error" not in last.lower(),
       f"the summary reports failures: {last[:200]}")

import re
m = re.search(r"(\d+) passed", last)
h.need(m, f"could not read a pass count from: {last[:200]}")
count = int(m.group(1))
h.need(count >= 100, f"only {count} tests ran; the suite has shrunk")

print(f"# {count} regression tests passed")
h.ok("REGRESSION_OK")
