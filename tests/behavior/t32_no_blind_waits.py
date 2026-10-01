"""G32: waits end when the condition is met, not when a timer expires."""
import time
import _harness as h

h.fresh_db()
import verify
from lib import shell
import subprocess, sys

# process_running with a long timeout must return IMMEDIATELY when already true
t0 = time.perf_counter()
ev = verify.process_running("python", timeout=30)
dt = time.perf_counter() - t0
h.need(ev.ok, "this python process was not found")
h.need(dt < 1.0, f"a condition already true still waited {dt:.1f}s")

# and a launch wait must return as soon as the process appears
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
t0 = time.perf_counter()
ev = verify.process_running(child.pid, timeout=30)
dt = time.perf_counter() - t0
h.need(ev.ok and dt < 2.0, f"waited {dt:.1f}s for a process already running")
shell.kill(child.pid)

# process_gone must return as soon as it is gone, not after the full timeout
t0 = time.perf_counter()
ev = verify.process_gone(child.pid, timeout=20)
dt = time.perf_counter() - t0
h.need(ev.ok, "the killed process was not seen to be gone")
h.need(dt < 5.0, f"waited {dt:.1f}s after the process had already died")

# a genuinely absent thing must still USE its timeout rather than hang forever
t0 = time.perf_counter()
ev = verify.process_running("no-such-process-zzz", timeout=2)
dt = time.perf_counter() - t0
h.need(not ev.ok, "a nonexistent process was found")
h.need(1.5 < dt < 6.0, f"the timeout was not honoured: {dt:.1f}s")

# a command that finishes fast must not be padded to its timeout
t0 = time.perf_counter()
r = shell.run(f'"{sys.executable}" -c "pass"', timeout=30)
dt = time.perf_counter() - t0
h.need(r["ok"] and dt < 5.0, f"a trivial command took {dt:.1f}s of a 30s budget")

h.ok("NO_BLIND_WAITS_OK")
