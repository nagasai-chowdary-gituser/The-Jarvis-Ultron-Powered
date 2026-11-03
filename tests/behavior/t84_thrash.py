"""G84: a tool hammered without finishing gets ONE step-back nudge, not minutes of looping.

The owner asked "what project is in VS Code" and it read file after file for three
minutes - a soft loop the exact-repeat detector misses because each read differs.
The thrash guard catches "one tool used too many times on an unfinished turn" and
nudges the model to the shortest route (the window title), once.
"""
import _harness as h

h.fresh_db()
import brain
from safety import Session, Transport

b = brain.Brain(Session(Transport.DESK), client=object())

# Below the limit: no nudge - ordinary multi-step work must not be interrupted.
b.tool_counts = {"run_python": brain.THRASH_LIMIT - 1, "recall": 2}
h.need(b._thrash_tool() is None, "nudged before it was actually thrashing")

# At the limit: the offending tool is named.
b.tool_counts = {"run_python": brain.THRASH_LIMIT, "recall": 2}
h.need(b._thrash_tool() == "run_python", "did not catch the thrashing tool")

# Only once - the guard can never become a loop itself.
b.thrash_nudged = True
h.need(b._thrash_tool() is None, "the thrash guard fired more than once")

# The nudge actually points at the shortest route.
msg = brain.THRASH_NUDGE.format(tool="run_python", n=7)
h.need("list_windows" in msg and "title" in msg.lower(),
       "the nudge does not point at the window-title shortcut")
h.need("thrash" in msg.lower(), "the nudge does not name the problem")

# dispatch counts tools, so the guard sees real usage.
b2 = brain.Brain(Session(Transport.DESK), client=object())
b2.dispatch("recall", {"topic": "anything"})
h.need(b2.tool_counts.get("recall", 0) == 1,
       f"dispatch did not count the tool call: {b2.tool_counts}")

h.ok("THRASH_OK")
