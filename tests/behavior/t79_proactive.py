"""G79: proactivity is bounded - Jarvis NOTICES and PROPOSES, never acts alone.

A human assistant brings things up nobody asked it to watch. This proves the
anticipation AND the rule that keeps it safe: on its own initiative Jarvis may
only propose; acting autonomously is gated to an explicitly opted-in whitelist
that is empty by default. Internal bookkeeping stays out of the prompt, and a
notice is raised once, not nagged.
"""
import _harness as h

h.fresh_db()
import proactive
import state

# 1. Bounded autonomy: nothing is autonomous by default; the gate is real.
h.need(proactive.may_act("archive_old_builds") is False,
       "an action was autonomous with an empty whitelist")
proactive.AUTONOMOUS_WHITELIST.add("archive_old_builds")
try:
    h.need(proactive.may_act("archive_old_builds") is True,
           "the whitelist did not open the gate")
    h.need(proactive.may_act("delete_files") is False,
           "a non-whitelisted action was allowed")
finally:
    proactive.AUTONOMOUS_WHITELIST.discard("archive_old_builds")

# 2. sweep is read-only and never raises, even where desktop stats cannot run.
s = proactive.sweep()
h.need(isinstance(s, list), "sweep did not return a list")
for n in s:
    h.need(isinstance(n.get("text"), str) and n["text"], "a notice had no text")

# 3. A run of failures becomes a proposal - surfaced ONCE, then deduped.
for _ in range(4):
    state.record_outcome("desk", "something", "timeout")
sw = proactive.sweep()
h.need(any(n["key"] == "reliability" for n in sw),
       f"a failure streak was not noticed: {sw}")
first = proactive.notices()
h.need(any(n["key"] == "reliability" for n in first),
       "notices did not surface the reliability proposal")
second = proactive.notices()
h.need(not any(n["key"] == "reliability" for n in second),
       "the same notice was raised again - that is nagging, not anticipating")

# 4. Internal bookkeeping never leaks into the prompt the Boss sees.
block = state.facts_block()
h.need("_pro" not in block and "pro:" not in block,
       f"proactive bookkeeping leaked into the prompt: {block!r}")

h.ok("PROACTIVE_OK")
