"""G19: two GUI operations never hold the desktop at the same time."""
import threading, time
import _harness as h

h.fresh_db()
import execution

concurrent = {"now": 0, "peak": 0}
lock = threading.Lock()

def gui(_):
    with lock:
        concurrent["now"] += 1
        concurrent["peak"] = max(concurrent["peak"], concurrent["now"])
    time.sleep(0.3)
    with lock:
        concurrent["now"] -= 1
    return "gui done"

steps = [execution.Step(id=f"g{i}", run=gui, resource=execution.DESKTOP)
         for i in range(4)]
r = execution.run_graph(steps)
h.need(all(x["ok"] for x in r["results"].values()), "a GUI step failed")
h.need(concurrent["peak"] == 1,
       f"{concurrent['peak']} GUI operations ran at once - they would fight "
       f"over the same window")
h.need(r["wall_seconds"] >= 1.1,
       f"they overlapped despite the lock: {r['wall_seconds']}s")

# the browser is a separate exclusive resource, also serialised
seen = {"peak": 0, "now": 0}
def br(_):
    with lock:
        seen["now"] += 1; seen["peak"] = max(seen["peak"], seen["now"])
    time.sleep(0.25)
    with lock:
        seen["now"] -= 1
    return 1
r2 = execution.run_graph([execution.Step(id=f"b{i}", run=br,
                                         resource=execution.BROWSER)
                          for i in range(3)])
h.need(seen["peak"] == 1, f"{seen['peak']} browser operations ran at once")

# but GUI work must NOT block unrelated free work
order = []
def gui_slow(_): time.sleep(0.6); order.append("gui"); return 1
def free_fast(_): time.sleep(0.1); order.append("free"); return 1
execution.run_graph([
    execution.Step(id="gui1", run=gui_slow, resource=execution.DESKTOP),
    execution.Step(id="gui2", run=gui_slow, resource=execution.DESKTOP),
    execution.Step(id="free1", run=free_fast),
])
h.need(order and order[0] == "free",
       f"free work queued behind the GUI lock: {order}")

h.ok("GUI_LOCK_OK")
