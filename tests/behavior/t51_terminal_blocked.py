"""G51: an unreachable objective ends BLOCKED, with the remedy stated."""
import _harness as h

h.fresh_db()
import budget

b = budget.Budget()
b.block("Your own Chrome has no debugging port, so I cannot read its tabs. "
        "Close it and start it with --remote-debugging-port=9223.")
v = b.verdict()
h.need(v is budget.Verdict.BLOCKED, f"verdict is {v.value}")
h.need(v.terminal, "BLOCKED is not terminal")
h.need(not v.is_failure,
       "BLOCKED was classed as a failure; it is an answer needing someone else")

msg = b.explain(v)
h.need("debugging port" in msg, f"the remedy was lost: {msg!r}")
h.need("ran out of steps" not in msg.lower(), "it still blames the step count")

# a blocker outranks a deadline, so the reported reason is the TRUE one
b2 = budget.Budget(deadline_s=0.001)
b2.block("the file is on a drive that is not mounted")
import time; time.sleep(0.05)
h.need(b2.verdict() is budget.Verdict.BLOCKED,
       "a timeout masked the real blocker")

# needing authorisation is its own terminal state, not a failure
b3 = budget.Budget()
b3.require_auth("permanently delete 400 files")
h.need(b3.verdict() is budget.Verdict.NEEDS_AUTH, "auth need not surfaced")
h.need("PIN" in b3.explain(budget.Verdict.NEEDS_AUTH), "no remedy for auth")
h.need(not budget.Verdict.NEEDS_AUTH.is_failure, "needing a PIN is not failure")

h.ok("TERMINAL_BLOCKED_OK")
