"""G49: a task needing far more rounds than the old limit still completes.

TWO EARLIER DESIGNS OF THIS CHECK WERE WRONG, and the reason matters.

    v1 asked for eight folders of files. The agent wrote one Python loop and
       did it in ONE call.
    v2 used a trail of files naming each other. The agent wrote a regex loop
       and did that in one call too.

Both were correct, efficient behaviour, and neither proved anything. Forcing
extra rounds with padding is exactly what the brief forbids.

What actually drives rounds is UNCERTAINTY: state the agent cannot predict and
must therefore observe between actions. So this drives a live media player
through a sequence of real state changes. Each answer depends on what the page
is doing at that moment, which no script written in advance can know."""
import time
import _harness as h

h.fresh_db()
import brain, browser
from safety import Session, Transport

OLD_ROUND_LIMIT = 12
OLD_CALL_LIMIT = 15

browser.ensure(auto_restart=True)
if not browser.page_for("music.youtube"):
    browser.goto("https://music.youtube.com/search?q=lofi", new_tab=True)
    time.sleep(6)

b = brain.Brain(Session(Transport.DESK), channel="chan:t49")
t0 = time.perf_counter()
reply = b.run(
    "Using the YouTube Music tab already open in the browser, do all of this "
    "and report what you actually observed at each stage, in order: "
    "1) report whether anything is playing right now; "
    "2) if nothing is playing, start something and confirm it started; "
    "3) report the track title and artist; "
    "4) report how many items are in the queue; "
    "5) pause it and confirm from the page that it really paused; "
    "6) resume it and confirm from the page that it really resumed; "
    "7) report the playback position in seconds; "
    "8) finally say whether the track is the same one as in step 3. "
    "Check the real page at every stage - do not assume anything carried over.")
dt = time.perf_counter() - t0

rounds = b.metrics.get("rounds", 0)
calls = b.metrics.get("tool_calls", 0)

h.need("ran out of steps" not in reply.lower(),
       f"the step-exhaustion message is back: {reply[:150]!r}")
h.need(b.metrics.get("ended") in ("completed", "asked", ""),
       f"it ended as {b.metrics.get('ended')!r}")

# it must genuinely have needed more than the old architecture allowed
h.need(calls > OLD_CALL_LIMIT or rounds > OLD_ROUND_LIMIT,
       f"only {calls} calls / {rounds} rounds; this does not exceed the old "
       f"limits of {OLD_CALL_LIMIT}/{OLD_ROUND_LIMIT}")

# and it must have actually driven the player, not just narrated
h.need(b.metrics.get("progress", 0) >= 3,
       f"only {b.metrics.get('progress')} real progress signals for an "
       f"eight-stage task - it did not do the work")

# the reply must reference several observed stages
low = reply.lower()
mentioned = sum(1 for w in ("play", "queue", "pause", "resume", "position",
                            "track", "title") if w in low)
h.need(mentioned >= 3, f"the reply covers too little of the task: {reply[:200]!r}")

print(f"# {dt:.1f}s  rounds={rounds} calls={calls} "
      f"progress={b.metrics.get('progress')} recoveries={b.metrics.get('recoveries')}")
print(f"# old limits: {OLD_ROUND_LIMIT} rounds / {OLD_CALL_LIMIT} calls")
print(f"# reply: {reply[:200]}")

h.ok("LONG_TASK_OK")
