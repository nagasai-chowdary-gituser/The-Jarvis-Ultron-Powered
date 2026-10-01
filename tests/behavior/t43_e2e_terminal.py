"""G43: a terminal + task-state workflow completes and is verified on disk."""
import sys, time
import _harness as h

h.fresh_db()
import brain, tasks, verify
from safety import Session, Transport
from tasks import State

ws = h.workspace()

# a real script with a real failing test in it
(ws / "calc.py").write_text(
    "def add(a, b):\n    return a - b          # wrong on purpose\n",
    encoding="utf-8")
(ws / "test_calc.py").write_text(
    "from calc import add\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n",
    encoding="utf-8")

tid = tasks.create("make the failing test pass", transport="desk")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING)

b = brain.Brain(Session(Transport.DESK), channel="chan:e2e3")
t0 = time.perf_counter()
reply = b.run(
    f"In your workspace folder '{ws.name}' there is calc.py and test_calc.py. "
    f"Run the test with pytest, see that it fails, fix the bug in calc.py, "
    f"and run it again until it passes. Tell me what the bug was.")
dt = time.perf_counter() - t0

# verified against the REAL result of running the tests, not against the reply
from lib import shell
r = shell.run(f'"{sys.executable}" -m pytest test_calc.py -q', cwd=str(ws),
              timeout=120)
h.need(r["exit_code"] == 0,
       f"the tests still fail after the agent said it was done.\n"
       f"reply: {reply[:150]!r}\nstdout: {r['stdout'][-300:]}")

source = (ws / "calc.py").read_text(encoding="utf-8")
h.need("a + b" in source.replace(" ", " "),
       f"the fix is not in the source: {source!r}")

tasks.move(tid, State.OBSERVING)
ev = verify.outcome(tid, verify.command_succeeded(r),
                    verify.file_contains(ws / "calc.py", "+"))
h.need(ev.ok, f"the outcome did not verify: {ev.why}")
h.need(tasks.get(tid)["state"] is State.COMPLETED,
       f"the task is {tasks.get(tid)['state']}")

print(f"# {dt:.1f}s, {b.metrics['tool_calls']} tools, "
      f"{b.metrics['model_calls']} model calls")

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("E2E_TERMINAL_OK")
