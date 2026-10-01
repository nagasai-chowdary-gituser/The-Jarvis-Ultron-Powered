"""G26: the right PIN opens one session; a wrong one opens none."""
import _harness as h

h.fresh_db()
import safety

h.need(not safety.trusted(), "a session was already open before authenticating")

for wrong in ("0000", "1234", "", h.TEST_PIN[:-1], h.TEST_PIN + "0", "abcd"):
    r = safety.authenticate(wrong)
    h.need(r["ok"] is False, f"the wrong PIN {wrong!r} was accepted")
    h.need(not safety.trusted(), f"{wrong!r} opened a session anyway")
    h.need(wrong not in str(r) or not wrong,
           f"the attempted PIN was echoed back: {r}")

r = safety.authenticate(h.TEST_PIN)
h.need(r["ok"] is True, f"the correct PIN was refused: {r}")
h.need(safety.trusted(), "the correct PIN did not open a session")
h.need(r["trusted_for_minutes"] == 30, f"session length is {r}")
h.need(h.TEST_PIN not in str(r), f"the PIN appears in the result: {r}")

left = safety.seconds_left()
# stored rounded to the whole second, so allow a second either side
h.need(29 * 60 < left <= 30 * 60 + 2, f"session length wrong: {left}s")

safety.end_session()
h.need(not safety.trusted(), "ending the session did not end it")

h.ok("PIN_AUTH_OK")
