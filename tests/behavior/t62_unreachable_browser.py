"""G62: an unreachable browser is reported exactly, never guessed at."""
import _harness as h

h.fresh_db()
import browser, budget

# attaching to a port with nothing on it must fail with a reason, not hang
r = browser.attach_to(59999)
h.need(r["ok"] is False, "it claimed to attach to a port with no browser")
h.need("nothing is listening" in r["error"], f"vague error: {r['error']!r}")

# and that becomes a BLOCKED terminal condition carrying the remedy,
# rather than a search that cannot succeed running until a counter expires
ctx = browser.browsing_context()
b = budget.Budget()
if not ctx["can_see_owner_browser"]:
    b.block(ctx["remedy"])
    v = b.verdict()
    h.need(v is budget.Verdict.BLOCKED, f"verdict is {v.value}")
    h.need(not v.is_failure, "an unreachable browser was classed as failure")
    msg = b.explain(v)
    h.need("remote-debugging-port" in msg or "debugging port" in msg,
           f"the remedy is not actionable: {msg[:120]!r}")
    h.need("ran out of steps" not in msg.lower(), "it blamed the step count")
else:
    # everything is reachable, so the honest thing is to say the check could
    # not exercise the blocked path rather than to pretend it did
    h.need(ctx["unreachable_chromes"] == 0, "context contradicts itself")

# asking for a tab that does not exist must say so, not invent one
m = browser.media_state("definitely-not-an-open-tab-xyz")
h.need(m["ok"] is False and m["found"] is False,
       f"it reported media from a tab that is not open: {m}")
h.need("no tab matching" in str(m.get("error", "")), f"vague: {m}")

h.ok("UNREACHABLE_BROWSER_OK")
