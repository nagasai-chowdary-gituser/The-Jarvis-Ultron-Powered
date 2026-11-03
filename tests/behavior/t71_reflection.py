"""G71: the reflection gate makes a real task PROVE its result before "done".

The old build's worst habit was declaring a job finished on a feeling. The gate
sends a real task back exactly once - to open it, read it, or LOOK at it - before
the turn may end. Trivial turns and pure questions are never nudged, so the check
costs nothing on the ninety-nine percent.
"""
import _harness as h

h.fresh_db()
import brain
import budget as budget_mod
from safety import Session, Transport

# A stub client: this check exercises _needs_verify, which never calls a model,
# so there is no reason to require a real key to prove the gate's logic.
b = brain.Brain(Session(Transport.DESK), client=object())

# A real task (heavy budget) that actually DID something is sent to verify once.
heavy = budget_mod.Budget(deadline_s=420.0)
heavy.did(productive=True)                      # actions = 1
b.verifications = 0
h.need(b._needs_verify(heavy) is True,
       "a real task that did work was allowed to finish with no self-check")

# ...but only once. After the single nudge it proceeds.
b.verifications = brain.MAX_VERIFY_NUDGES
h.need(b._needs_verify(heavy) is False,
       "the gate nudged more than once - that risks a verify-loop")

# A trivial turn (small budget) is never nudged - the check must stay invisible
# on simple requests.
b.verifications = 0
trivial = budget_mod.Budget(deadline_s=60.0)
trivial.did(productive=True)
h.need(b._needs_verify(trivial) is False, "a trivial turn was sent to verify")

# A pure question (no actions) has nothing to open and look at.
b.verifications = 0
qa = budget_mod.Budget(deadline_s=300.0)        # actions = 0
h.need(b._needs_verify(qa) is False,
       "a pure question was nudged with nothing to verify")

# The nudge itself demands EVIDENCE, and the cap is one.
h.need(brain.MAX_VERIFY_NUDGES == 1, "the reflection cap is not one")
low = brain.VERIFY_NUDGE.lower()
h.need("evidence" in low and ("open" in low or "read" in low or "see_screen" in low),
       "the verify nudge does not actually ask for grounded evidence")

h.ok("REFLECTION_OK")
