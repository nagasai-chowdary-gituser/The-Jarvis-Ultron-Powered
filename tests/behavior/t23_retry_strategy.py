"""G23: a repeated identical failure forces a DIFFERENT approach."""
import _harness as h

h.fresh_db()
import safety, tasks
from safety import Blocked, Session, Transport
from tasks import State

# the gate stops the same call repeating forever
s = Session(Transport.DESK)
sig = "run_python:{'code': 'broken'}"
for i in range(safety.MAX_RETRIES - 1):
    safety.check("run_python", s, sig)
    s.note(sig)

try:
    safety.check("run_python", s, sig)
    h.fail("the same failing call was allowed a third identical time")
except Blocked as b:
    h.need("different" in b.spoken.lower(),
           f"the refusal did not ask for a different method: {b.spoken!r}")

# and the task records WHAT was tried, so the next choice can differ
tid = tasks.create("reach the page")
tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)

options = ["direct http", "browser DOM", "browser after refresh"]
first = tasks.next_strategy(tid, options)
h.need(first == "direct http", f"unexpected first choice: {first!r}")
tasks.note_attempt(tid, first)

second = tasks.next_strategy(tid, options)
h.need(second != first, "it chose the same strategy twice")
h.need(second == "browser DOM", f"unexpected second choice: {second!r}")
tasks.note_attempt(tid, second)

third = tasks.next_strategy(tid, options)
h.need(third not in (first, second), f"repeated a tried strategy: {third!r}")
tasks.note_attempt(tid, third)

h.need(tasks.next_strategy(tid, options) is None,
       "it offered a strategy after every option was spent")
h.need(tasks.already_tried(tid, "direct http"), "the attempt log lost an entry")
h.need(tasks.get(tid)["attempts"] == 3, "the attempt count is wrong")
h.need(tasks.out_of_attempts(tid), "it did not notice the attempts were spent")

h.ok("RETRY_STRATEGY_OK")
