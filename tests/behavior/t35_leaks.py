"""G35: repeated heavy cycles leak no processes, threads or browser contexts."""
import gc, sys, threading, time
import _harness as h

h.fresh_db()
import psutil
import execution, tasks, verify
from lib import files, shell
from tasks import State

me = psutil.Process()
gc.collect()
t0_threads = threading.active_count()
t0_kids = len(me.children(recursive=True))
t0_handles = me.num_handles() if hasattr(me, "num_handles") else 0

ws = h.workspace()
for cycle in range(6):
    # a full cycle: task, files, subprocess, parallel graph, verify, cancel
    tid = tasks.create(f"cycle {cycle}")
    tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)

    files.write(f"{ws.name}/c{cycle}.txt", "x" * 500)
    r = shell.run(f'"{sys.executable}" -c "print({cycle})"')
    h.need(r["ok"], f"cycle {cycle}: the command failed")

    execution.run_graph([execution.Step(id=f"s{i}", run=lambda i: 1)
                         for i in range(4)])

    tasks.move(tid, State.OBSERVING)
    verify.outcome(tid, verify.file_exists(ws / f"c{cycle}.txt"))
    h.need(tasks.get(tid)["state"] is State.COMPLETED, "cycle did not complete")

    dead = tasks.create(f"cancelled {cycle}")
    tasks.move(dead, State.PLANNING)
    tasks.cancel(dead)

tasks.sweep_stop_events()
gc.collect()
time.sleep(1.5)

threads = threading.active_count()
kids = len(me.children(recursive=True))
h.need(threads <= t0_threads + 3,
       f"threads leaked over 6 cycles: {t0_threads} -> {threads}")
h.need(kids <= t0_kids, f"child processes leaked: {t0_kids} -> {kids}")
h.need(len(tasks._stop_events) <= 2,
       f"cancellation Events leaked: {len(tasks._stop_events)} left")

if t0_handles:
    handles = me.num_handles()
    h.need(handles < t0_handles + 400,
           f"OS handles leaked: {t0_handles} -> {handles}")

# browser contexts must not multiply across repeated use
import browser
browser.ensure(auto_restart=True)
before_tabs = len(browser.tabs().get("tabs", []))
for i in range(3):
    browser.goto("https://example.com/", new_tab=True)
    browser.close_tabs("example.com")
after_tabs = len(browser.tabs().get("tabs", []))
h.need(after_tabs <= before_tabs + 1,
       f"browser tabs leaked: {before_tabs} -> {after_tabs}")

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("LEAKS_OK")
