"""G3: a task interrupted mid-flight is picked up again, not left stuck."""
import time
import _harness as h

h.fresh_db()
import tasks
from tasks import State

# a task that "was running" when the process died
stuck = tasks.create("interrupted work")
tasks.move(stuck, State.PLANNING)
tasks.move(stuck, State.EXECUTING)

# a task that is genuinely still being worked on right now
live = tasks.create("current work")
tasks.move(live, State.PLANNING)
tasks.move(live, State.EXECUTING)

# age only the first one
c = tasks._conn()
c.execute("UPDATE tasks SET updated_at=? WHERE id=?",
          (time.time() - 3600, stuck))
c.commit(); c.close()

revived = tasks.recover_interrupted(stale_after_s=60)
h.need(stuck in revived, "the interrupted task was not recovered")
h.need(live not in revived, "a task still being worked on was disturbed")
h.need(tasks.get(stuck)["state"] is State.RECOVERING,
       f"stuck task is {tasks.get(stuck)['state']}, expected RECOVERING")
h.need(tasks.get(live)["state"] is State.EXECUTING,
       "the live task was moved when it should not have been")

# one that has used every attempt must fail honestly instead of looping
spent = tasks.create("exhausted work")
tasks.move(spent, State.PLANNING)
tasks.move(spent, State.EXECUTING)
for i in range(tasks.MAX_ATTEMPTS):
    tasks.note_attempt(spent, f"strategy {i}")
c = tasks._conn()
c.execute("UPDATE tasks SET updated_at=? WHERE id=?",
          (time.time() - 3600, spent))
c.commit(); c.close()
tasks.recover_interrupted(stale_after_s=60)
t = tasks.get(spent)
h.need(t["state"] is State.FAILED, f"exhausted task is {t['state']}")
h.need(t["error"], "a failed task carries no error text")

h.ok("RECOVERY_OK")
