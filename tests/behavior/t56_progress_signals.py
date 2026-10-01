"""G56: progress is a real outcome, not the fact that a tool ran."""
import _harness as h

h.fresh_db()
import brain, budget

# things that are NOT progress, however busy they look
for tool, args, result in [
    ("run_python", {"code": "from lib import files\nprint(files.ls('.'))"},
     {"ok": True, "stdout": ""}),                      # ran, printed nothing
    ("ask_boss", {"question": "which one?"}, {"ok": True}),
    ("run_python", {"code": "x = 1"}, {"ok": False, "error": "boom"}),
    ("recall", {"topic": "nothing"}, {"ok": True}),
]:
    h.need(brain._progress_kind(tool, args, result) is None,
           f"{tool} with {result} was counted as progress")

# things that ARE progress
cases = [
    ("run_python", {"code": "from lib import files\nfiles.write('a','b')"},
     {"ok": True, "stdout": "wrote"}, "artifact"),
    ("run_python", {"code": "from lib import desktop\ndesktop.open_app('x')"},
     {"ok": True, "stdout": "launched"}, "state_change"),
    ("run_python", {"code": "import browser\nbrowser.goto('u')"},
     {"ok": True, "stdout": "ok"}, "state_change"),
    ("run_python", {"code": "print(2+2)"}, {"ok": True, "stdout": "4"},
     "discovery"),
    ("schedule_job", {"kind": "reminder"}, {"ok": True, "job_id": 3}, "subgoal"),
]
for tool, args, result, expected in cases:
    got = brain._progress_kind(tool, args, result)
    h.need(got == expected, f"{tool}: got {got}, expected {expected}")

# the Progress ledger only accepts known signals
p = budget.Progress()
h.need(p.record("artifact", "x") is True, "a real signal was rejected")
h.need(p.record("vibes", "x") is False, "an invented signal was accepted")
h.need(p.count == 1, f"the ledger counted {p.count}")

h.ok("PROGRESS_SIGNALS_OK")
