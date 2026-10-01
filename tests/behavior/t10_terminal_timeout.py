"""G10: a timeout kills the whole tree and leaves no orphan."""
import sys, time
import _harness as h

h.fresh_db()
import psutil
from lib import shell

before = {p.pid for p in psutil.Process().children(recursive=True)}

t0 = time.perf_counter()
r = shell.run(f'"{sys.executable}" -c "import time; time.sleep(120)"', timeout=3)
elapsed = time.perf_counter() - t0

h.need(r["timed_out"] is True, f"the timeout was not reported: {r}")
h.need(r["ok"] is False, "a timed-out command reported success")
h.need(elapsed < 25, f"the timeout did not bound the wait: {elapsed:.1f}s")
h.need("timed out" in (r.get("error") or "").lower(), "no timeout reason given")

# a shell that spawns a grandchild - killing only the shell leaves it running
code = ("import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        "time.sleep(120)")
t0 = time.perf_counter()
r = shell.run(f'"{sys.executable}" -c "{code}"', timeout=4)
h.need(r["timed_out"], "the nested case did not time out")

time.sleep(2)
after = {p.pid for p in psutil.Process().children(recursive=True)}
leaked = after - before
still = [p for p in psutil.process_iter(["pid", "name"]) if p.pid in leaked]
h.need(not still, f"orphaned processes survived the timeout: "
                  f"{[(p.pid, p.info.get('name')) for p in still]}")

h.ok("TERMINAL_TIMEOUT_OK")
