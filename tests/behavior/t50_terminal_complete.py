"""G50: completion is reached by verifying the objective, and is terminal."""
import _harness as h

h.fresh_db()
import budget, tasks, verify
from tasks import State

ws = h.workspace()

b = budget.Budget()
h.need(b.verdict() is budget.Verdict.CONTINUE, "a fresh budget is not runnable")

tid = tasks.create("produce the artefact")
tasks.move(tid, State.PLANNING); tasks.move(tid, State.EXECUTING)
b.round_started(); b.did(); b.progress.record("artifact", "wrote it")

(ws / "out.txt").write_text("done", encoding="utf-8")
tasks.move(tid, State.OBSERVING)
ev = verify.outcome(tid, verify.file_exists(ws / "out.txt"))
h.need(ev.ok, f"the real artefact did not verify: {ev.why}")
h.need(tasks.get(tid)["state"] is State.COMPLETED,
       f"verified work is {tasks.get(tid)['state']}, not COMPLETED")
h.need(tasks.get(tid)["state"].terminal, "COMPLETED is not terminal")

# and a claimed completion with NO artefact must not complete
tid2 = tasks.create("claim without doing")
tasks.move(tid2, State.PLANNING); tasks.move(tid2, State.EXECUTING)
tasks.move(tid2, State.OBSERVING)
ev2 = verify.outcome(tid2, verify.file_exists(ws / "never.txt"))
h.need(not ev2.ok, "a missing artefact verified")
h.need(tasks.get(tid2)["state"] is not State.COMPLETED,
       "an unverified task reached COMPLETED")

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("TERMINAL_COMPLETE_OK")
