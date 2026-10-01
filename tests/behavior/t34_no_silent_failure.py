"""G34: a failure in a real subsystem is RECORDED, never just swallowed."""
import _harness as h

h.fresh_db()
import state, brain, execution
from safety import Session, Transport

state.clear_errors()

# 1. a HUD emit that raises must not break the turn AND must be recorded
def exploding_emit(ev):
    raise RuntimeError("the display socket died")

b = brain.Brain(Session(Transport.DESK), emit=exploding_emit,
                client=type("C", (), {"responses": None})())
b._say({"type": "test"})                     # must not raise
errs = state.recent_errors(20)
h.need(any("brain.emit" in e["where_"] for e in errs),
       f"a broken display was swallowed silently: {[e['where_'] for e in errs]}")

# 2. a storage failure while remembering a turn must be recorded
state.clear_errors()
real_add = state.add_message
state.add_message = lambda *a, **k: (_ for _ in ()).throw(IOError("disk full"))
try:
    brain._remember_turn("chan:x", "in", "hello")   # must not raise
finally:
    state.add_message = real_add
errs = state.recent_errors(20)
h.need(any("remember_turn" in e["where_"] for e in errs),
       "a lost conversation turn vanished without trace")
h.need(any(e["kind"] == "OSError" for e in errs),
       f"the error type was lost: {[(e['kind'], e['where_']) for e in errs]}")

# 3. a raising tool inside the parallel executor is captured as a failure,
#    not dropped, and the OTHER calls still complete
state.clear_errors()
def dispatch(tool, args):
    if args.get("code") == "boom":
        raise RuntimeError("tool exploded")
    return {"ok": True}

calls = [execution.Call(id="a", tool="run_python", args={"code": "boom"}),
         execution.Call(id="b", tool="run_python", args={"code": "fine"})]
execution.run_calls(calls, dispatch)
h.need(calls[0].result and calls[0].result["ok"] is False,
       f"a raising tool reported success: {calls[0].result}")
h.need("tool exploded" in str(calls[0].result.get("error", "")),
       f"the real reason was lost: {calls[0].result}")
h.need(calls[1].result["ok"] is True, "an unrelated call was lost")

# 4. the ledger must be bounded, not an unbounded growth path
for i in range(state.KEEP_ERRORS + 50):
    state.record_error("flood", ValueError(f"e{i}"))
h.need(len(state.recent_errors(10_000)) <= state.KEEP_ERRORS,
       "the error ledger grows without bound")

# 5. recording must never itself raise, whatever it is handed
state.record_error("weird", None)
state.record_error("weird", "a plain string")
state.record_error("x" * 500, ValueError("y" * 5000))

h.ok("NO_SILENT_FAILURE_OK")
