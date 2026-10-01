"""G2: task state survives a restart - read back by a SEPARATE process."""
import subprocess, sys
import _harness as h

db = h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("survive a restart", transport="telegram")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING, action="step two of four")
tasks.note_attempt(tid, "first strategy")

# A genuinely separate interpreter, reading the same file. In-process caching
# would make this pass even if nothing were ever written.
code = (
    "import sys; sys.path.insert(0, r'%s');"
    "import state, tasks; from pathlib import Path;"
    "state.DB_PATH = Path(r'%s'); tasks.reset_for_tests();"
    "t = tasks.get(%d);"
    "print(t['state'].value, t['action'], t['attempts'], t['transport'])"
) % (str(h.ROOT), str(db), tid)
out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                     text=True, timeout=60)
h.need(out.returncode == 0, f"reader process failed: {out.stderr[-300:]}")
got = out.stdout.strip()
h.need(got.startswith("executing"), f"state not persisted: {got!r}")
h.need("step two of four" in got, f"action not persisted: {got!r}")
h.need(" 1 " in f" {got} ", f"attempt count not persisted: {got!r}")
h.need("telegram" in got, f"transport not persisted: {got!r}")

h.ok("PERSISTENCE_OK")
