"""G27: ordinary work runs with ZERO prompts, session or not."""
import _harness as h

h.fresh_db()
import safety
from safety import Session, Transport

# The point the owner made three times: normal work must never ask.
safety.end_session()
h.need(not safety.trusted(), "expected no session for this half of the test")

ordinary = [
    "open chrome", "read my downloads folder", "search the web for pandas",
    "message Uma on whatsapp", "run my tests", "what is my cpu usage",
    "create a file called notes.txt", "organise my downloads",
]
for request in ordinary:
    h.need(safety.risk_of(request) is None,
           f"ordinary work was classified as high risk: {request!r}")
    safety.authorize(safety.risk_of(request) or "none")   # must not raise

# every tool, every transport, no session: nothing may prompt
for transport in Transport:
    s = Session(transport)
    for tool in ("run_python", "recall", "schedule_job", "ask_boss"):
        safety.check(tool, s)              # raising here would be a prompt
        s.calls += 1

# and inside a session, still nothing
safety.authenticate(h.TEST_PIN)
h.need(safety.trusted(), "the session did not open")
for transport in Transport:
    s = Session(transport)
    for tool in ("run_python", "recall"):
        safety.check(tool, s)
        s.calls += 1
for request in ordinary:
    safety.authorize(safety.risk_of(request) or "none")

h.ok("SESSION_REUSE_OK")
