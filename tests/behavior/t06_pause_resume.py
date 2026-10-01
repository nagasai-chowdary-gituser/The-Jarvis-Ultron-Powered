"""G6: pause stops the work and resume continues from where it stopped."""
import threading, time
import _harness as h

h.fresh_db()
import tasks
from tasks import State

tid = tasks.create("pausable work")
tasks.move(tid, State.PLANNING)
tasks.move(tid, State.EXECUTING)

done = []
run = threading.Event(); run.set()

def work():
    for i in range(60):
        while tasks.pause_requested(tid):
            run.clear()
            time.sleep(0.05)
        run.set()
        done.append(i)
        time.sleep(0.05)

th = threading.Thread(target=work, daemon=True)
th.start()
time.sleep(0.35)

tasks.pause(tid)
h.need(tasks.get(tid)["state"] is State.PAUSED, "state did not become PAUSED")
time.sleep(0.4)
at_pause = len(done)
h.need(at_pause > 0, "nothing ran before the pause")
time.sleep(0.4)
h.need(len(done) == at_pause,
       f"work continued while paused: {at_pause} -> {len(done)}")

tasks.resume(tid)
h.need(tasks.get(tid)["state"] is State.EXECUTING,
       "resume did not return the task to EXECUTING")
time.sleep(0.5)
h.need(len(done) > at_pause, "work did not continue after resume")
# it must CONTINUE, not restart from zero
h.need(done[:at_pause] == list(range(at_pause)) and done[at_pause] == at_pause,
       f"work restarted instead of continuing: {done[:at_pause+2]}")

h.ok("PAUSE_RESUME_OK")
