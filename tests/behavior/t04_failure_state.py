"""G4: failure is explicit, terminal, and carries the real reason."""
import _harness as h

h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("something that will fail")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING)
tasks.move(tid, State.FAILED, note="the target host refused the connection",
           error="ConnectionRefusedError: [Errno 111] refused")

t = tasks.get(tid)
h.need(t["state"] is State.FAILED, "state is not FAILED")
h.need(t["state"].terminal, "FAILED is not terminal")
h.need("ConnectionRefused" in (t["error"] or ""),
       f"the real error was lost: {t['error']!r}")

# a failed task cannot quietly become complete later
try:
    tasks.move(tid, State.COMPLETED)
    h.fail("a FAILED task was allowed to become COMPLETED")
except tasks.IllegalTransition:
    pass

# escalation is a DIFFERENT outcome from failure and must not be conflated
esc = tasks.create("needs a human")
tasks.move(esc, State.PLANNING)
tasks.move(esc, State.ESCALATED, note="the site requires a human CAPTCHA")
e = tasks.get(esc)
h.need(e["state"] is State.ESCALATED, "escalation did not stick")
h.need(e["state"] is not State.FAILED, "escalation was recorded as failure")
h.need(e["state"].terminal, "ESCALATED is not terminal")

h.ok("FAILURE_STATE_OK")
