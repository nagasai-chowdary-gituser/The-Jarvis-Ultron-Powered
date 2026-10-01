"""G31: deterministic requests are answered with NO model call at all."""
import time
import _harness as h

h.fresh_db()
import brain, fastpath
from safety import Session, Transport

class ExplodingClient:
    """Any attempt to reach a model during the fast path fails the check."""
    class responses:
        @staticmethod
        def create(**kw):
            raise AssertionError("the fast path called a model")

for request in ("what time is it", "cpu usage", "whats running"):
    b = brain.Brain(Session(Transport.DESK), client=ExplodingClient())
    t0 = time.perf_counter()
    reply = b.run(request)
    dt = time.perf_counter() - t0
    h.need(b.metrics["model_calls"] == 0,
           f"{request!r} made {b.metrics['model_calls']} model calls")
    h.need(b.metrics["fastpath"] is True, f"{request!r} did not take the fast path")
    h.need(reply and not reply.lower().startswith("[guess] i could not"),
           f"{request!r} produced no real answer: {reply!r}")
    h.need(dt < 3.0, f"{request!r} took {dt:.1f}s on the fast path")

# and the answers are REAL, measured from the machine
b = brain.Brain(Session(Transport.DESK), client=ExplodingClient())
reply = b.run("cpu usage")
import psutil
h.need("percent" in reply.lower(), f"no measurement in: {reply!r}")

# anything ambiguous must NOT take the fast path
for request in ("open chrome and then message uma", "why is my project failing",
                "delete my files", "send a message to uma"):
    h.need(fastpath.match(request) is None,
           f"an ambiguous request took the fast path: {request!r}")

h.ok("FASTPATH_OK")
