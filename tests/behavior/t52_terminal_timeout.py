"""G52: a wall-clock deadline ends the task and says so honestly."""
import time
import _harness as h

h.fresh_db()
import budget

b = budget.Budget(deadline_s=0.4)
b.round_started(); b.did(); b.progress.record("discovery", "found one thing")
h.need(b.verdict() is budget.Verdict.CONTINUE, "it expired before its deadline")

time.sleep(0.5)
v = b.verdict()
h.need(v is budget.Verdict.TIMEOUT, f"verdict is {v.value}, expected timeout")
h.need(v.terminal and v.is_failure, "timeout must be terminal and a failure")

msg = b.explain(v)
h.need("time limit" in msg.lower(), f"the reason is not the deadline: {msg!r}")
h.need("1 thing" in msg or "1 thing(s)" in msg,
       f"it did not report the work already done: {msg!r}")
h.need("ran out of steps" not in msg.lower(), "it blamed the step count")

# the deadline must be sized to the work, not one small number for everything
simple = budget.for_request("open notepad")
complex_ = budget.for_request(
    "research three frameworks, compare them, and save a summary to my documents")
h.need(complex_.deadline_s > simple.deadline_s * 3,
       f"complex work got {complex_.deadline_s}s vs simple {simple.deadline_s}s")
h.need(simple.deadline_s <= 90, f"a trivial request got {simple.deadline_s}s")
h.need(complex_.runaway_rounds > simple.runaway_rounds,
       "complex work was not given more room")

h.ok("TERMINAL_TIMEOUT_OK")
