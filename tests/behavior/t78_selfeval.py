"""G78: Jarvis measures its own reliability - a counted rate, not a guess.

The build knew its cost to six decimals and its success rate not at all. Now every
real turn records how it ended and success_summary reads it back, with the right
accounting: completed is a win, timeout/stuck are failures, and blocked/needs_auth
are handoffs that needed a human - answers, not failures (§5.1).
"""
import _harness as h

h.fresh_db()
import state

# Empty is honest: no rate, not a made-up one.
s = state.success_summary()
h.need(s["rate"] is None and s["total"] == 0,
       f"an empty history did not report honestly: {s}")
h.need("no success rate" in s["spoken"].lower(),
       f"the empty spoken line invents something: {s['spoken']!r}")

# Record a realistic mix.
for v in ("completed", "completed", "completed", "completed",
          "timeout", "stuck",            # two real failures
          "blocked", "needs_auth"):      # two handoffs - not failures
    state.record_outcome("desk", f"do the {v} thing", v, actions=2, elapsed=1.0)

s = state.success_summary(days=7)
# 4 wins out of 6 decidable (4 completed + 2 failed) = 67%; handoffs excluded.
h.need(s["completed"] == 4, f"wins miscounted: {s}")
h.need(s["failures"] == 2, f"failures miscounted: {s}")
h.need(s["handoffs"] == 2, f"handoffs miscounted: {s}")
h.need(abs(s["rate"] - 4 / 6) < 0.001,
       f"the rate is wrong - handoffs must not count as failures: {s['rate']}")
h.need("67%" in s["report"], f"the report does not show the rate: {s['report']!r}")

# A secret in a request text is never stored (redact runs on the way in).
import os
if (os.environ.get("JARVIS_PIN") or ""):
    pin = os.environ["JARVIS_PIN"]
    state.record_outcome("desk", f"my pin is {pin} log me in", "completed")
    blob = " ".join(str(r["request"]) for r in
                    state._conn().execute("SELECT request FROM outcomes").fetchall())
    h.need(pin not in blob, "a PIN survived into the outcomes table")

h.ok("SELFEVAL_OK")
