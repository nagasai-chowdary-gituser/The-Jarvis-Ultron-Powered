"""G48: no legitimate task is ended by a low-level action count."""
import inspect, re
import _harness as h

h.fresh_db()
import brain, budget, safety

# the old mechanism must be gone from the engine, not merely raised
src = inspect.getsource(brain)
h.need(not hasattr(brain, "MAX_ITERATIONS"),
       "brain still exposes MAX_ITERATIONS")
h.need("for _ in range(" not in src.split("def run")[-1],
       "the execution loop is still a fixed range()")

# and the message itself must be unreachable: no verdict produces it
for v in budget.Verdict:
    if v is budget.Verdict.CONTINUE:
        continue
    b = budget.Budget()
    text = b.explain(v)
    h.need("ran out of steps" not in text.lower(),
           f"verdict {v.value} still blames the step count: {text!r}")

# a budget that has done a LOT of productive work must still say CONTINUE
b = budget.Budget(deadline_s=600)
for i in range(120):
    b.round_started() if i % 4 == 0 else None
    b.did(productive=True)
    b.progress.record("state_change", f"step {i}")
h.need(b.actions == 120, f"actions not counted: {b.actions}")
h.need(b.verdict() is budget.Verdict.CONTINUE,
       f"120 productive actions ended the task: {b.verdict().value}")

# the runaway ceiling is a CEILING, far above real work
h.need(safety.RUNAWAY_TOOL_CALLS >= 200,
       f"the safety ceiling is {safety.RUNAWAY_TOOL_CALLS}, low enough to cut "
       f"off real tasks")
h.need(budget.Budget().runaway_rounds >= 30,
       "the default runaway round ceiling is too low to be a ceiling")

# and the old 15-call cap must not be enforced anywhere
from safety import Session, Transport
s = Session(Transport.DESK)
for i in range(60):
    safety.check("run_python", s, f"sig-{i}")   # unique args: not a loop
    s.calls += 1
h.need(s.calls == 60, "a session was cut off well before the ceiling")

h.ok("NO_STEP_LIMIT_OK")
