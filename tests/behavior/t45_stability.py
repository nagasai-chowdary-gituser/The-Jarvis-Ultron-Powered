"""G45: the same objective run repeatedly gives the same verified outcome."""
import time
import _harness as h

h.fresh_db()
import brain, verify
from safety import Session, Transport

RUNS = 3
timings, tools = [], []

for i in range(RUNS):
    ws = h.workspace()
    b = brain.Brain(Session(Transport.DESK), channel=f"chan:stab{i}")
    t0 = time.perf_counter()
    b.run(f"Create a file called run.txt in your workspace folder "
          f"'{ws.name}' containing exactly the word STABLE.")
    timings.append(time.perf_counter() - t0)
    tools.append(b.metrics["tool_calls"])

    ev = verify.all_of(verify.file_exists(ws / "run.txt"),
                       verify.file_contains(ws / "run.txt", "STABLE"))
    h.need(ev.ok, f"run {i+1} of {RUNS} did not produce the artefact: {ev.why}")

    import shutil; shutil.rmtree(ws, ignore_errors=True)

h.need(len(timings) == RUNS, "not every run completed")
spread = max(timings) - min(timings)
h.need(max(timings) < 60, f"a run took {max(timings):.0f}s")
h.need(spread < 45, f"wildly inconsistent timing: {[round(t,1) for t in timings]}")

# no state bleeds between runs
import state
h.need(len(state.recent_errors(50)) == 0 or
       all("brain.emit" in e["where_"] for e in state.recent_errors(50)),
       f"errors accumulated across runs: "
       f"{[e['where_'] for e in state.recent_errors(5)]}")

print(f"# runs {[round(t,1) for t in timings]}s, tools {tools}")
h.ok("STABILITY_OK")
