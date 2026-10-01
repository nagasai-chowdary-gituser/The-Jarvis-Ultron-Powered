"""G18: independent work really overlaps and finishes faster than serially."""
import time
import _harness as h

h.fresh_db()
import execution

def slow(_):
    time.sleep(0.5)
    return "done"

steps = [execution.Step(id=f"s{i}", run=slow) for i in range(4)]
r = execution.run_graph(steps)
h.need(all(x["ok"] for x in r["results"].values()), f"a step failed: {r}")
h.need(r["peak_parallel"] == 4, f"peak was {r['peak_parallel']}, expected 4")
h.need(r["wall_seconds"] < 1.0,
       f"it did not overlap: {r['wall_seconds']}s wall vs "
       f"{r['serial_seconds']}s serial")
h.need(r["serial_seconds"] > 1.8, "the work was too quick to prove anything")

# the same via run_calls, which the brain uses for tool batches
calls = [execution.Call(id=str(i), tool="run_python", args={"code": "x=1"})
         for i in range(4)]
rep = execution.run_calls(calls, lambda t, a: time.sleep(0.4) or {"ok": True})
h.need(rep.widest == 4, f"run_calls widest was {rep.widest}")
h.need(rep.saved_seconds > 0.8, f"no time saved: {rep.saved_seconds}")

h.ok("PARALLEL_OK")
