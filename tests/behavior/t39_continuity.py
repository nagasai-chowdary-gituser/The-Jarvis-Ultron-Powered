"""G39: the conversation survives separate turns AND a restart."""
import subprocess, sys
import _harness as h

db = h.fresh_db()
import brain, state

CHAN = "chan:continuity"

# separate Brain objects, exactly as main.py builds one per message
state.add_message(CHAN, "in", "my project deadline is the 30th of November")
state.add_message(CHAN, "out", "Noted - 30 November.")

turns = brain._recent_turns(CHAN)
h.need(len(turns) == 2, f"history did not load: {turns}")
h.need(turns[0]["role"] == "user" and turns[1]["role"] == "assistant",
       f"roles are wrong: {[t['role'] for t in turns]}")
h.need("November" in turns[0]["content"], "the content was lost")

# only plain text - replaying tool-call items is a 400 from the API
for t in turns:
    h.need(set(t) == {"role", "content"}, f"a non-replayable item: {t}")

# a NEW Brain must start with that history already loaded
from safety import Session, Transport
b = brain.Brain(Session(Transport.TELEGRAM), channel=CHAN, client=object())
h.need(len(b.convo) == 2, f"a fresh Brain started with {len(b.convo)} turns")
h.need("November" in b.convo[0]["content"], "the fresh Brain lost the content")

# each door keeps its own thread
state.add_message("chan:phone", "in", "phone-only line")
tg = " ".join(t["content"] for t in brain._recent_turns(CHAN))
ph = " ".join(t["content"] for t in brain._recent_turns("chan:phone"))
h.need("phone-only" not in tg, "a phone line leaked into the telegram thread")
h.need("November" not in ph, "a telegram line leaked into the phone thread")

# and it survives a genuinely separate process
code = ("import sys; sys.path.insert(0, r'%s');"
        "import state; from pathlib import Path;"
        "state.DB_PATH = Path(r'%s'); state._READY=set();"
        "import brain;"
        "print(len(brain._recent_turns('%s')), brain._recent_turns('%s')[0]['content'][:60])"
        ) % (str(h.ROOT), str(db), CHAN, CHAN)
out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                     text=True, encoding="utf-8", errors="replace", timeout=90)
h.need(out.returncode == 0, f"the reader process failed: {out.stderr[-300:]}")
h.need(out.stdout.strip().startswith("2"),
       f"history did not survive the restart: {out.stdout!r}")
h.need("November" in out.stdout, f"content lost across restart: {out.stdout!r}")

h.ok("CONTINUITY_OK")
