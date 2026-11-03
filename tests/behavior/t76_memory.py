"""G76: memory LEARNS the Boss - it is not just told, and it recalls by relevance.

facts/ answers "what is his college". This proves the human part: a preference
stated in passing is captured, saying it again reinforces it rather than
duplicating, the right memory surfaces for the request in front of it, and a
credential is never memorised.
"""
import _harness as h

h.fresh_db()
import state

# 1. Learn, and REINFORCE on repeat instead of duplicating.
a = state.learn("preference", "I like my slide decks dark and under 10 slides")
b = state.learn("preference", "i like my slide decks dark and under 10 slides")
h.need(a == b, "a repeated preference was stored twice, not reinforced")
prefs = state.memories("preference")
h.need(len(prefs) == 1 and prefs[0]["weight"] == 2,
       f"reinforcement did not raise the weight: {prefs}")

# 2. Recall by RELEVANCE - the deck preference comes back for a deck request,
#    the unrelated ones do not crowd it out.
state.learn("person", "my manager is Priya")
state.learn("preference", "always send invoices as PDF")
rel = state.recall_relevant("make me a slide deck for tomorrow")
joined = " ".join(m["text"].lower() for m in rel)
h.need("dark" in joined, f"the deck preference was not recalled for a deck: {rel}")
h.need("invoice" not in joined, f"an irrelevant memory was surfaced: {rel}")

# 3. The prompt block is built only when something relevant exists.
block = state.relevant_block("slide deck")
h.need("WHAT YOU KNOW ABOUT THE BOSS" in block and "dark" in block,
       f"the relevant-memory block is wrong: {block!r}")
h.need(state.relevant_block("xyzzy nothing matches here") == "",
       "a block was built with nothing actually relevant")

# 4. Auto-capture catches a stated preference, ignores idle chatter.
state.extract_and_learn("from now on always greet me as Chief")
h.need(any("chief" in m["text"].lower() for m in state.memories()),
       "auto-capture missed a clear 'from now on' preference")
before = len(state.memories())
state.extract_and_learn("what is the weather like today")
h.need(len(state.memories()) == before, "auto-capture stored idle chatter")

# 5. A secret is NEVER memorised - by either path.
state.learn("fact", "my api key is sk-abcdef1234567890ABCDEF")
state.extract_and_learn("i always use my password hunter2 everywhere")
blob = " ".join(m["text"] for m in state.memories()).lower()
h.need("sk-" not in blob and "hunter2" not in blob,
       f"a credential was written into memory: {blob!r}")

h.ok("MEMORY_OK")
