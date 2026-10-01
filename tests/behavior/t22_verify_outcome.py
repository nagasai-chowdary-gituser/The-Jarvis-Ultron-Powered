"""G22: a claimed outcome is checked against the real world before reporting."""
import subprocess, sys, time
import _harness as h

h.fresh_db()
import tasks, verify
from lib import shell
from tasks import State

ws = h.workspace()

# a file that does NOT exist must not verify
ev = verify.file_exists(ws / "nope.txt")
h.need(not ev.ok, "a missing file verified as present")
h.need("nothing exists" in ev.why, f"no useful reason given: {ev.why!r}")

# an EMPTY file must not count as a successful download
(ws / "empty.pdf").write_bytes(b"")
ev = verify.file_exists(ws / "empty.pdf", min_bytes=1)
h.need(not ev.ok, "a zero-byte file passed as a real download")
h.need("0 bytes" in ev.why or "only 0" in ev.why, f"reason: {ev.why!r}")

# the wrong TYPE must not pass either
(ws / "page.html").write_text("<html>not a pdf</html>", encoding="utf-8")
ev = verify.file_exists(ws / "page.html", min_bytes=5, suffix=".pdf")
h.need(not ev.ok, "an html file verified as a pdf")
h.need(".html" in ev.why, f"reason did not name what was found: {ev.why!r}")

# a real one passes, with evidence
(ws / "real.pdf").write_bytes(b"%PDF-1.4" + b"x" * 3000)
ev = verify.file_exists(ws / "real.pdf", min_bytes=1000, suffix=".pdf")
h.need(ev.ok and ev.found["bytes"] > 1000, f"a real file failed: {ev.why}")

ev = verify.file_contains(ws / "page.html", "not a pdf")
h.need(ev.ok, "content check failed on content that is there")
h.need(not verify.file_contains(ws / "page.html", "absent text").ok,
       "content check passed for text that is absent")

# commands
h.need(verify.command_succeeded(shell.run(f'"{sys.executable}" -c "pass"')).ok,
       "a successful command did not verify")
bad = shell.run(f'"{sys.executable}" -c "import sys; sys.exit(4)"')
ev = verify.command_succeeded(bad)
h.need(not ev.ok and "exited 4" in ev.why, f"a failing command verified: {ev.why}")
slow = shell.run(f'"{sys.executable}" -c "import time; time.sleep(30)"', timeout=2)
h.need(not verify.command_succeeded(slow).ok, "a timed-out command verified")

# processes
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
h.need(verify.process_running(child.pid, timeout=5).ok, "a live process did not verify")
shell.kill(child.pid)
h.need(verify.process_gone(child.pid, timeout=10).ok, "a killed process still verified")
h.need(not verify.process_running("definitely-not-a-process-zzz").ok,
       "a nonexistent process verified as running")

# all_of reports the FIRST real reason, not a count
combo = verify.all_of(verify.file_exists(ws / "real.pdf"),
                      verify.file_exists(ws / "nope.txt"))
h.need(not combo.ok and "nothing exists" in combo.why,
       f"combined failure lost the reason: {combo.why!r}")

# and a task only reaches COMPLETED through a verified outcome
tid = tasks.create("produce the report")
tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)
tasks.move(tid, State.OBSERVING)
bad_ev = verify.outcome(tid, verify.file_exists(ws / "nope.txt"))
h.need(not bad_ev.ok, "an unmet outcome verified")
h.need(tasks.get(tid)["state"] is State.RECOVERING,
       f"a failed verification left the task {tasks.get(tid)['state']}")

good = verify.outcome(tid, verify.file_exists(ws / "real.pdf", min_bytes=1000))
h.need(good.ok, f"a met outcome did not verify: {good.why}")
h.need(tasks.get(tid)["state"] is State.COMPLETED,
       f"a verified task is {tasks.get(tid)['state']}, not COMPLETED")
h.need(tasks.get(tid)["result"], "COMPLETED carries no evidence")

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("VERIFY_OUTCOME_OK")
