"""G57: recovery has its OWN purse and cannot eat the productive one."""
import _harness as h

h.fresh_db()
import budget

b = budget.Budget(recovery_budget=8, deadline_s=600)

# eight failures must NOT stop a task that is otherwise getting somewhere
for i in range(8):
    b.recovered()
    b.did(productive=True)
    b.progress.record("state_change", f"recovered and advanced {i}")
h.need(b.recoveries == 8, f"recoveries counted as {b.recoveries}")
h.need(b.actions == 8, "productive actions were consumed by the recoveries")
h.need(b.verdict() is budget.Verdict.CONTINUE,
       "eight recoveries ended a task that was still advancing")

# past its own budget, recovery alone is what stops it - with a true reason
b.recovered()
v = b.verdict()
h.need(v is budget.Verdict.STUCK, f"verdict is {v.value}")
h.need("different ways" in b.explain(v), f"unclear reason: {b.explain(v)!r}")

# the two counters are genuinely independent
b2 = budget.Budget(recovery_budget=3)
for _ in range(50):
    b2.did(productive=True)
    b2.progress.record("artifact", "more work")
h.need(b2.recoveries == 0, "productive work was counted as recovery")
h.need(b2.verdict() is budget.Verdict.CONTINUE,
       "fifty productive actions tripped the recovery budget")

# complex work is given a bigger recovery purse than trivial work
h.need(budget.for_request("research and compare three options and save it"
                          ).recovery_budget
       > budget.for_request("open notepad").recovery_budget,
       "a complex task got no more room to recover than a trivial one")

h.ok("RECOVERY_BUDGET_OK")
