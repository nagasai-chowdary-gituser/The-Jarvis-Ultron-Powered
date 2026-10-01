"""G13: processes are listed, inspected and genuinely terminated."""
import subprocess, sys, time
import _harness as h

h.fresh_db()
import psutil
from lib import desktop, shell

procs = desktop.processes()
h.need(len(procs) > 5, f"only {len(procs)} processes listed")
h.need(all("pid" in p and "name" in p for p in procs), "process rows malformed")

me = desktop.processes("python")
h.need(me, "this very python process was not found in the listing")

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
time.sleep(0.6)
h.need(shell.alive(child.pid), "the child did not start")

r = shell.kill(child.pid)
h.need(r["ok"] is True, f"kill reported failure: {r}")
deadline = time.time() + 10
while time.time() < deadline and shell.alive(child.pid):
    time.sleep(0.2)
h.need(not shell.alive(child.pid), "the process survived kill()")

# a tree must die together
parent_code = ("import subprocess, sys, time;"
               "subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']);"
               "time.sleep(120)")
p = subprocess.Popen([sys.executable, "-c", parent_code])
time.sleep(1.5)
kids = [c.pid for c in psutil.Process(p.pid).children(recursive=True)]
h.need(kids, "the grandchild never started, so tree-kill proves nothing")
shell.kill(p.pid, tree=True)
time.sleep(1.5)
survivors = [k for k in kids if shell.alive(k)]
h.need(not survivors, f"grandchildren survived a tree kill: {survivors}")

# Jarvis must refuse to kill itself
import os
r = shell.kill(os.getpid())
h.need(r["ok"] is False, "Jarvis agreed to kill its own process")

h.ok("PROCESS_OK")
