"""G53: cancellation outranks every other budget and stops work at once."""
import threading, time
import _harness as h

h.fresh_db()
import budget, tasks
from tasks import State

tid = tasks.create("long work")
tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)

b = budget.Budget(deadline_s=600, task_id=tid)
h.need(b.verdict() is budget.Verdict.CONTINUE, "not runnable at the start")

# cancellation reaches the budget through the task, across the process
tasks.cancel(tid, reason="owner said stop")
v = b.verdict()
h.need(v is budget.Verdict.CANCELLED, f"verdict is {v.value}")
h.need(v.terminal, "CANCELLED is not terminal")
h.need("stopped" in b.explain(v).lower(), f"unclear message: {b.explain(v)!r}")

# it outranks a blocker, a deadline and an auth need - the owner's word wins
b2 = budget.Budget(deadline_s=0.001)
b2.block("something else"); b2.require_auth("something"); b2.cancel()
time.sleep(0.05)
h.need(b2.verdict() is budget.Verdict.CANCELLED,
       f"cancellation was outranked by {b2.verdict().value}")

# and real running work stops at its checkpoint
tid2 = tasks.create("cancellable loop")
tasks.move(tid2, State.PLANNING); tasks.move(tid2, State.EXECUTING)
done, stopped = [], {}
def work():
    try:
        for i in range(200):
            tasks.checkpoint(tid2)
            done.append(i); time.sleep(0.02)
    except tasks.Cancelled:
        stopped["at"] = len(done)
th = threading.Thread(target=work); th.start()
time.sleep(0.3)
tasks.cancel(tid2)
th.join(timeout=10)
h.need("at" in stopped, "the work was never interrupted")
h.need(len(done) < 200, "the work ran to completion despite cancelling")
h.need(tasks.get(tid2)["state"] is State.CANCELLED, "state is not CANCELLED")

h.ok("TERMINAL_CANCEL_OK")
