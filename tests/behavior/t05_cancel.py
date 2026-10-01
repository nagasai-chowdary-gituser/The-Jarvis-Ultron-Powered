"""G5: cancelling actually stops running work."""
import threading, time
import _harness as h

h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("a long piece of work")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING)

steps_done = []
stopped_at = {}

def work():
    try:
        for i in range(100):
            tasks.checkpoint(tid)          # the cooperative stop point
            steps_done.append(i)
            time.sleep(0.05)
    except tasks.Cancelled:
        stopped_at["step"] = len(steps_done)

th = threading.Thread(target=work)
th.start()
time.sleep(0.4)                            # let it get genuinely underway
started = len(steps_done)
h.need(started > 0, "the work never started, so cancelling proves nothing")

tasks.cancel(tid, reason="owner said stop")
th.join(timeout=10)
h.need(not th.is_alive(), "the worker thread did not stop")

h.need("step" in stopped_at, "the work was never actually interrupted")
h.need(len(steps_done) < 100, "the work ran to completion despite cancelling")
h.need(len(steps_done) < started + 20,
       f"it kept going long after cancel: {started} -> {len(steps_done)}")

t = tasks.get(tid)
h.need(t["state"] is State.CANCELLED, f"state is {t['state']}, not CANCELLED")
h.need(t["state"].terminal, "CANCELLED is not terminal")

# a cancelled task must not be resurrectable into success
try:
    tasks.move(tid, State.COMPLETED)
    h.fail("a CANCELLED task was allowed to become COMPLETED")
except tasks.IllegalTransition:
    pass

h.ok("CANCEL_OK")
