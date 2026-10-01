"""G66: the exact request that failed now ends on a real terminal condition.

The owner's words, typos and all, because that is what was actually typed:

    "my chrome has youtube tab that playing the song so tell me whasts
     next song in qyues"

It answered: "[state] I ran out of steps before finishing that."

A REGULAR youtube.com TAB, not YouTube Music. The first repair of this was
built and tested entirely against music.youtube.com, which has a queue list
that names itself; ordinary youtube.com has none, so the reader that "worked"
would have found nothing on the page the owner was actually looking at.

The acceptance here is NOT that a particular song comes back - the queue is
live and changes between runs. It is that the task ends for a reason that is
TRUE about the work, that step exhaustion is no longer one of the reasons
available, and that no track is named which is not really on screen.
"""
import time
import _harness as h

h.fresh_db()
import brain, browser, budget
from safety import Session, Transport

# the owner's situation: a youtube tab, playing
browser.ensure(auto_restart=True)
if not browser.page_for("www.youtube.com/watch"):
    browser.goto("https://www.youtube.com/watch?v=jfKfPfyJRdk", new_tab=True)
    time.sleep(9)

b = brain.Brain(Session(Transport.TELEGRAM), channel="chan:t66")
t0 = time.perf_counter()
reply = b.run("my chrome has youtube tab that playing the song so tell me "
              "whasts next song in qyues")
dt = time.perf_counter() - t0
low = reply.lower().replace("’", "'")

# THE regression: this sentence must never be produced again
h.need("ran out of steps" not in low,
       f"the step-exhaustion message is BACK: {reply!r}")

# it must have ended on a named, legitimate condition
ended = b.metrics.get("ended")
h.need(ended, f"the turn ended with no recorded verdict: {b.metrics}")
legitimate = {"completed", "blocked", "cancelled", "timeout", "needs_auth",
              "stuck", "runaway"}
h.need(ended in legitimate, f"unknown terminal condition: {ended!r}")
h.need(ended != "runaway", "the engine looped until its safety ceiling")

# whatever it concluded, it must have actually looked at the browser
h.need(b.metrics["tool_calls"] >= 1, "it answered without inspecting anything")

# and the answer must be about the queue, or say precisely why it cannot be
answered = any(w in low for w in ("next", "queue", "playing", "track", "song"))
explained = any(w in low for w in ("cannot", "can't", "could not", "couldn't",
                                    "not playing", "debugging port", "blocked"))
h.need(answered or explained,
       f"the reply is neither an answer nor an explanation: {reply[:200]!r}")

# It must have read THE TAB HE MEANT. He said "youtube tab"; a YouTube Music
# tab is also open and idle, and the first version of this answered from that
# one - naming a track from a paused player as "next in your queue". The union
# of both queues would have let that pass, so the live data is taken from the
# page that is actually playing, and only that page.
yt = browser.media_state("youtube.com")
h.need(yt.get("ok"), f"could not read the youtube tab afterwards: {yt}")
h.need("music." not in (yt.get("url") or ""),
       f"the youtube tab resolved to a different host: {yt.get('url')!r}")

live = [row["text"] for row in (yt.get("queue") or [])]
if yt.get("title"):
    live.append(yt["title"])

if live and any(w in low for w in ("next", "queue", "playing")):
    said = {w for w in low.replace(",", " ").replace(".", " ").split() if len(w) > 4}
    real = {w for row in live for w in row.lower().split() if len(w) > 4}
    h.need(said & real or explained,
           f"it named a track that is not on the tab he asked about.\n"
           f"reply: {reply[:200]!r}\nlive: {live[:5]}")

# And it must not sell a suggestion as a queue. When the page exposes no queue
# list, the honest answer says so; "the next song in your queue is X" would be
# false in a way the person listening cannot possibly catch.
if yt.get("queue_source") == "same-shape-links" and live:
    hedge = ("suggest", "autoplay", "auto-play", "recommend", "up next",
             "no queue", "not a queue", "no actual queue", "no real queue",
             "does not expose", "doesn't expose", "no visible queue")
    h.need(any(w in low for w in hedge),
           "the page exposes no queue, yet the reply presented its rows as "
           f"one: {reply[:200]!r}")

print(f"# {dt:.1f}s ended={ended!r} tools={b.metrics['tool_calls']} "
      f"rounds={b.metrics.get('rounds')} progress={b.metrics.get('progress')}")
print(f"# tab={yt.get('url','')[:44]} via={yt.get('queue_source')}")
print(f"# reply: {reply[:200]}")

h.ok("ORIGINAL_FAILURE_OK")
