"""G54: an identical action repeating with the same outcome is a loop."""
import _harness as h

h.fresh_db()
import budget

d = budget.LoopDetector(limit=3)
args = {"code": "browser.goto('https://x')"}

h.need(not d.looping("run_python", args, "failed"), "a first try is not a loop")
d.note("run_python", args, "failed")
d.note("run_python", args, "failed")
h.need(not d.looping("run_python", args, "failed"), "two tries is not yet a loop")
d.note("run_python", args, "failed")
h.need(d.looping("run_python", args, "failed"), "three identical failures is a loop")
h.need(d.any_loop, "the loop was not recorded")

# SAME action, DIFFERENT outcome, is progress - not a loop
d2 = budget.LoopDetector(limit=3)
for outcome in ("track 1", "track 2", "track 3", "track 4"):
    d2.note("run_python", {"code": "next()"}, outcome)
h.need(not d2.any_loop,
       "clicking next four times was called a loop; each did something different")

# different arguments are different actions
d3 = budget.LoopDetector(limit=3)
for i in range(5):
    d3.note("run_python", {"code": f"step {i}"}, "ok")
h.need(not d3.any_loop, "five different actions were called a loop")

# a loop must not itself terminate the task - it forces a CHANGE
b = budget.Budget()
for _ in range(5):
    b.loops.note("run_python", args, "failed")
h.need(b.loops.any_loop, "the budget did not see the loop")
h.need(b.verdict() is budget.Verdict.CONTINUE,
       "detecting a loop ended the task instead of prompting a different way")

h.ok("LOOP_DETECTION_OK")
