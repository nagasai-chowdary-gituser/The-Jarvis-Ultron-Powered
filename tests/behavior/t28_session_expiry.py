"""G28: the trusted session really expires after 30 minutes."""
import _harness as h

h.fresh_db()
import state, safety

safety.authenticate(h.TEST_PIN)
h.need(safety.trusted(), "the session did not open")

# high risk is permitted while trusted
safety.authorize("permanent_delete")

# wind the clock past the window rather than waiting half an hour
import time
state.set_fact(safety.SESSION_KEY, f"{time.time() - 1:.0f}")
h.need(not safety.trusted(), "an expired session is still trusted")
h.need(safety.seconds_left() == 0, f"time left after expiry: {safety.seconds_left()}")

try:
    safety.authorize("permanent_delete")
    h.fail("a high-risk action was allowed after the session expired")
except safety.Blocked as b:
    h.need("PIN" in b.spoken, f"the refusal does not say what is needed: {b.spoken!r}")

# ordinary work must STILL be unaffected by expiry
for request in ("open chrome", "read a file", "search the web"):
    safety.authorize(safety.risk_of(request) or "none")

# and re-authenticating restores it
safety.authenticate(h.TEST_PIN)
h.need(safety.trusted(), "re-authentication failed")
safety.authorize("permanent_delete")

# a session just inside the window is still valid
state.set_fact(safety.SESSION_KEY, f"{time.time() + 5:.0f}")
h.need(safety.trusted(), "a session with 5s left was treated as expired")

h.ok("SESSION_EXPIRY_OK")
