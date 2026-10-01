"""G55: rounds that achieve nothing are detected and stop the repetition."""
import _harness as h

h.fresh_db()
import budget

b = budget.Budget(max_barren_rounds=4, deadline_s=600)
for i in range(3):
    b.round_started(); b.did(productive=False)
    b.progress.barren_round()
h.need(b.verdict() is budget.Verdict.CONTINUE,
       "it gave up after three fruitless rounds, before its own limit")

b.progress.barren_round()
v = b.verdict()
h.need(v is budget.Verdict.STUCK, f"verdict is {v.value}, expected stuck")
h.need("changed anything" in b.explain(v), f"unclear message: {b.explain(v)!r}")
h.need("ran out of steps" not in b.explain(v).lower(), "it blamed the count")

# ONE piece of real progress resets the streak - a task getting somewhere
# slowly must not be killed for being slow
b2 = budget.Budget(max_barren_rounds=4)
for _ in range(3):
    b2.progress.barren_round()
h.need(b2.progress.rounds_without == 3, "the streak was not counted")
b2.progress.record("artifact", "a file appeared")
h.need(b2.progress.rounds_without == 0, "real progress did not reset the streak")
for _ in range(3):
    b2.progress.barren_round()
h.need(b2.verdict() is budget.Verdict.CONTINUE,
       "the streak was not really reset")

h.ok("NO_PROGRESS_OK")
