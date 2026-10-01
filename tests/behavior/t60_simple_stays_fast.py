"""G60: removing the cap must not turn simple requests into planning festivals."""
import time
import _harness as h

h.fresh_db()
import brain, budget
from safety import Session, Transport

# the budget itself must be sized down for a trivial request
small = budget.for_request("open notepad")
big = budget.for_request("research three options, compare them and save a summary")
h.need(small.deadline_s <= 90, f"a trivial request got {small.deadline_s}s")
h.need(small.runaway_rounds <= 15, f"trivial ceiling is {small.runaway_rounds}")
h.need(big.deadline_s > small.deadline_s * 3, "complex work got no more room")
h.need(big.runaway_rounds > small.runaway_rounds * 2, "ceilings do not scale")

# and the fast path must still answer deterministic things with NO model call
class Exploding:
    class responses:
        @staticmethod
        def create(**kw):
            raise AssertionError("a trivial request reached a model")

for q in ("what time is it", "cpu usage", "whats running"):
    b = brain.Brain(Session(Transport.DESK), client=Exploding())
    t0 = time.perf_counter()
    reply = b.run(q)
    dt = time.perf_counter() - t0
    h.need(b.metrics["model_calls"] == 0, f"{q!r} called a model")
    h.need(dt < 3.0, f"{q!r} took {dt:.1f}s")
    h.need(reply.strip(), f"{q!r} produced nothing")

# a real simple task through the model must stay cheap
b = brain.Brain(Session(Transport.DESK), channel="chan:t60")
t0 = time.perf_counter()
reply = b.run("How many characters are in the word extraordinary?")
dt = time.perf_counter() - t0
h.need("13" in reply, f"wrong answer: {reply[:100]!r}")
h.need(b.metrics.get("rounds", 99) <= 4,
       f"a one-line question took {b.metrics.get('rounds')} rounds")
h.need(dt < 45, f"a one-line question took {dt:.1f}s")

print(f"# simple question: {dt:.1f}s, {b.metrics.get('rounds')} rounds, "
      f"{b.metrics['tool_calls']} tools")
h.ok("SIMPLE_STAYS_FAST_OK")
