"""G21: concurrency is bounded; no unlimited workers are ever created."""
import threading, time
import _harness as h

h.fresh_db()
import execution

live = {"now": 0, "peak": 0}
lock = threading.Lock()

def work(_):
    with lock:
        live["now"] += 1; live["peak"] = max(live["peak"], live["now"])
    time.sleep(0.2)
    with lock:
        live["now"] -= 1
    return 1

# thirty independent steps must NOT become thirty threads
r = execution.run_graph([execution.Step(id=f"s{i}", run=work)
                         for i in range(30)], max_workers=4)
h.need(all(x["ok"] for x in r["results"].values()), "a step failed")
h.need(live["peak"] <= 4,
       f"{live['peak']} ran at once against a limit of 4")
h.need(r["peak_parallel"] <= 4, f"reported peak {r['peak_parallel']} exceeds 4")
h.need(len(r["results"]) == 30, "not every step ran")

before = threading.active_count()
execution.run_graph([execution.Step(id=f"x{i}", run=work) for i in range(20)],
                    max_workers=3)
time.sleep(1.0)
after = threading.active_count()
h.need(after <= before + 2,
       f"threads leaked: {before} -> {after}")

h.need(execution.MAX_WORKERS <= 8,
       f"the default worker cap is {execution.MAX_WORKERS}, which is not bounded")

h.ok("BOUNDED_OK")
