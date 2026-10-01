"""G1: a task really moves through its states and every move is persisted."""
import _harness as h

h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("organise the downloads folder", transport="desk")
h.need(tasks.get(tid)["state"] is State.CREATED, "new task is not CREATED")

for s in (State.PLANNING, State.EXECUTING, State.OBSERVING,
          State.VERIFYING, State.COMPLETED):
    tasks.move(tid, s, note=f"-> {s.value}")
    h.need(tasks.get(tid)["state"] is s, f"state did not become {s.value}")

t = tasks.get(tid)
h.need(t["state"].terminal, "COMPLETED is not terminal")

# every transition must be on disk, in order, not just in memory
ev = tasks.history(tid)
seen = [e["to_"] for e in ev]
h.need(seen == ["created", "planning", "executing", "observing",
                "verifying", "completed"], f"history wrong: {seen}")

# an illegal move must raise, not silently succeed
try:
    tasks.move(tid, State.EXECUTING)
    h.fail("a terminal task accepted a further transition")
except tasks.IllegalTransition:
    pass

# and a move the machine forbids from a live state must also raise
t2 = tasks.create("second")
try:
    tasks.move(t2, State.COMPLETED)      # CREATED -> COMPLETED is not allowed
    h.fail("CREATED jumped straight to COMPLETED")
except tasks.IllegalTransition:
    pass

h.ok("LIFECYCLE_OK")
