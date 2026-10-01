"""G41: a real multi-step objective is planned and completed on the machine."""
import time
from pathlib import Path
import _harness as h

h.fresh_db()
import brain
from safety import Session, Transport

ws = h.workspace()
b = brain.Brain(Session(Transport.DESK), channel="chan:e2e1")

t0 = time.perf_counter()
reply = b.run(
    f"In your workspace folder '{ws.name}' do all four of these: "
    f"1) create a folder called reports, "
    f"2) write three files into it named a.txt, b.txt and c.txt, each "
    f"containing the number of characters in its own name, "
    f"3) count how many .txt files ended up in reports, "
    f"4) write that count into reports/total.txt. "
    f"Then tell me the count.")
dt = time.perf_counter() - t0

reports = ws / "reports"
h.need(reports.is_dir(), "the reports folder was never created")
for name in ("a.txt", "b.txt", "c.txt"):
    h.need((reports / name).is_file(), f"{name} is missing")
total = reports / "total.txt"
h.need(total.is_file(), "total.txt was never written")

written = total.read_text(encoding="utf-8").strip()
actual = len([p for p in reports.glob("*.txt") if p.name != "total.txt"])
h.need(str(actual) in written,
       f"total.txt says {written!r} but there are {actual} files")
h.need(str(actual) in reply or "three" in reply.lower() or "3" in reply,
       f"the reply does not carry the count: {reply[:120]!r}")

h.need(b.metrics["tool_calls"] >= 1, "nothing was actually executed")
print(f"# {dt:.1f}s, {b.metrics['tool_calls']} tool calls, "
      f"{b.metrics['model_calls']} model calls")

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("E2E_MULTISTEP_OK")
