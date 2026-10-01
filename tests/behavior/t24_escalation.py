"""G24: retries are bounded and a genuinely blocked task asks for help."""
import _harness as h

h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("log in to the site")
tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)

for strategy in ("saved cookie", "password login", "browser profile"):
    tasks.note_attempt(tid, strategy)
h.need(tasks.out_of_attempts(tid), "attempts were not bounded")

t = tasks.escalate(tid, need="solve the CAPTCHA once in the open browser",
                   done_so_far="everything up to the login page is ready")
h.need(t["state"] is State.ESCALATED, f"state is {t['state']}")
h.need(t["state"].terminal, "ESCALATED is not terminal")
h.need(t["state"] is not State.FAILED,
       "escalation was recorded as failure, which hides the ask")

ev = tasks.history(tid)[-1]
h.need("CAPTCHA" in (ev["note"] or ""),
       f"the escalation does not say what is needed: {ev['note']!r}")
h.need(t["result"], "escalation threw away the work already done")

# an escalation with no stated need is a bug, not a message
tid2 = tasks.create("something else")
tasks.move(tid2, State.PLANNING)
for blank in ("", "   "):
    try:
        tasks.escalate(tid2, need=blank)
        h.fail("escalated without saying what was needed")
    except ValueError:
        pass

# and an escalated task cannot later claim success
try:
    tasks.move(tid, State.COMPLETED)
    h.fail("an ESCALATED task was allowed to become COMPLETED")
except tasks.IllegalTransition:
    pass

h.ok("ESCALATION_OK")
