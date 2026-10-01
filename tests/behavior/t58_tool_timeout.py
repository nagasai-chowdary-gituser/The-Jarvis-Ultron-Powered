"""G58: a blocked tool times out into recovery; the task is not frozen."""
import sys, time
import _harness as h

h.fresh_db()
import budget
from lib import shell

b = budget.Budget(deadline_s=600)

# a tool that would hang for two minutes must not hold the task
t0 = time.perf_counter()
r = shell.run(f'"{sys.executable}" -c "import time; time.sleep(120)"', timeout=3)
elapsed = time.perf_counter() - t0
h.need(r["timed_out"] is True, f"the tool did not time out: {r}")
h.need(elapsed < 25, f"the task was frozen for {elapsed:.1f}s")

# and that becomes a RECOVERY, not a task-ending event
b.recovered()
h.need(b.verdict() is budget.Verdict.CONTINUE,
       "one tool timeout ended the whole task")
h.need(b.recoveries == 1, "the timeout was not counted as a recovery attempt")

# the task can then succeed by another route - the point of not freezing
r2 = shell.run(f'"{sys.executable}" -c "print(\'recovered\')"', timeout=30)
h.need(r2["ok"] and "recovered" in r2["stdout"],
       f"the alternative route failed too: {r2}")
b.did(productive=True); b.progress.record("state_change", "took another route")
h.need(b.verdict() is budget.Verdict.CONTINUE, "it stopped after recovering")
h.need(b.progress.count == 1, "the successful recovery was not recorded")

# nothing is left running afterwards
import psutil
time.sleep(1)
h.need(not [p for p in psutil.Process().children(recursive=True)],
       "the timed-out tool left a child process behind")

h.ok("TOOL_TIMEOUT_OK")
