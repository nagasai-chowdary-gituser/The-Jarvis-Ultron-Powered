"""G7: cancelling kills the child process and leaves nothing running."""
import subprocess, sys, time
import _harness as h

h.fresh_db()
import psutil
import tasks
from tasks import State

tid = tasks.create("work that spawns a child")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING)

# a real child that would otherwise outlive the cancel by 5 minutes
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
tasks.register_child(tid, child.pid)
time.sleep(0.4)
h.need(psutil.pid_exists(child.pid), "the child never started")
h.need(child.poll() is None, "the child exited on its own")

before = len(psutil.Process().children(recursive=True))
tasks.cancel(tid, reason="stop everything")

deadline = time.time() + 15
while time.time() < deadline and psutil.pid_exists(child.pid):
    time.sleep(0.2)

h.need(not psutil.pid_exists(child.pid) or child.poll() is not None,
       "the child process survived cancellation - it is an orphan")
h.need(tasks.get(tid)["state"] is State.CANCELLED, "state is not CANCELLED")

try:
    child.wait(timeout=5)
except Exception:
    child.kill()

time.sleep(0.4)
after = len(psutil.Process().children(recursive=True))
h.need(after <= before, f"child processes leaked: {before} -> {after}")

# the stop-event map must not grow forever
tasks.sweep_stop_events()
h.need(tid not in tasks._stop_events,
       "the cancellation Event for a finished task was never released")

h.ok("CANCEL_CLEANUP_OK")
