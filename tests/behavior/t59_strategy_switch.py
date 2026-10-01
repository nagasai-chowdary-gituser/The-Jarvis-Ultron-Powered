"""G59: a failing strategy is swapped, not repeated."""
import _harness as h

h.fresh_db()
import safety, tasks
from safety import Blocked, Session, Transport

# the gate pushes toward a DIFFERENT method rather than the same one again
s = Session(Transport.DESK)
sig = "run_python:{'code': 'the same failing thing'}"
safety.check("run_python", s, sig); s.note(sig)
safety.check("run_python", s, sig); s.note(sig)
try:
    safety.check("run_python", s, sig)
    h.fail("the same failing call was allowed a third time unchanged")
except Blocked as b:
    h.need("different" in b.spoken.lower(),
           f"the refusal does not ask for another method: {b.spoken!r}")

# a DIFFERENT approach is not blocked by the previous one's failures
safety.check("run_python", s, "run_python:{'code': 'a different approach'}")

# and the task remembers what has been tried, so the next pick differs
tid = tasks.create("read the page")
options = ["DOM selector", "accessibility labels", "rendered text",
           "real input events"]
picked = []
for _ in range(4):
    nxt = tasks.next_strategy(tid, options)
    h.need(nxt is not None, "ran out of strategies too early")
    h.need(nxt not in picked, f"offered {nxt!r} twice")
    picked.append(nxt); tasks.note_attempt(tid, nxt)
h.need(picked == options, f"strategies came out of order: {picked}")
h.need(tasks.next_strategy(tid, options) is None,
       "it offered a fifth strategy from a list of four")

# the real browser reader escalates through named strategies
import inspect, browser
src = inspect.getsource(browser.read_dynamic)
for stage in ("selector", "aria", "rendered-text"):
    h.need(stage in src, f"the reader has no {stage} stage")
h.need("attempts" in src, "the reader does not report what it tried")

h.ok("STRATEGY_SWITCH_OK")
