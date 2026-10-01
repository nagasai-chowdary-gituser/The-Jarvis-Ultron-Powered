"""G11: a real app launches and is CONFIRMED by process state, not assumed."""
import time
import _harness as h

h.fresh_db()
import psutil
from lib import desktop, shell

def running(name):
    n = name.lower()
    return [p.info["pid"] for p in psutil.process_iter(["pid", "name"])
            if n in (p.info.get("name") or "").lower()]

for pid in running("notepad"):            # clean slate
    shell.kill(pid)
time.sleep(1)
h.need(not running("notepad"), "notepad was still running before the test")

r = desktop.open_app("Notepad")
h.need(r.get("ok") is not False, f"launch reported failure: {r}")
h.need(r.get("verified") is True,
       f"the launch was reported without verifying the process: {r}")
h.need(r.get("pid"), f"no pid was captured, so nothing was confirmed: {r}")

pids = running("notepad")
h.need(pids, "open_app returned success but no notepad process exists")

# and the reported pid must be one that is genuinely alive
h.need(shell.alive(r["pid"]), f"the reported pid {r['pid']} is not running")

for pid in pids:
    shell.kill(pid)
time.sleep(1)
h.need(not running("notepad"), "notepad survived being killed")

h.ok("APP_LAUNCH_OK")
