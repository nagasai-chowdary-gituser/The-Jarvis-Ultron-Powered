"""
models.py — every model id and price, as constants. One file.

Also holds the tier choice, which used to be router.py. It is fifteen lines of
keyword matching and it does not deserve a module: the old one was a file, an
import, a test file and a doc section to express "hard work should not start on
the weakest setting".

VERIFIED LIVE, not from memory: terra and luna are on this account. astra and
sol are not.
"""
from __future__ import annotations

BRAIN = "gpt-5.6-luna"              # $0.20 / $0.02 cached / $1.20 per 1M
# The orchestrator brain. Upgraded 2026-10-29: gpt-6.1-terra is ~50% cheaper than
# gpt-5.6-terra ($1/$5 vs $2/$12) AND smarter - OpenAI reports +15pts on Agents'
# Last Exam at ~1/3 the cost and the error rate falling from 53.6% to 16.7%. So
# this one change lowers the bill AND reduces the misreads at once.
BRAIN_HEAVY = "gpt-6.1-terra"       # $1.00 / $0.10 cached / $5.00 per 1M
REALTIME = "gpt-realtime-mini"      # the phone and desk voice; 0.76s to first word
VISION = "gpt-5.6-luna"
VISION_FAST = "gpt-4o-mini"         # eyes: screen/image reading - 5x faster than luna, same accuracy on text
EMBED = "text-embedding-3-small"    # intelligent memory: semantic recall (1536-dim)

MAX_OUTPUT_TOKENS = 4000
DAILY_CAP_USD = 5.0

PRICING = {
    BRAIN:       {"in": 0.20, "cached": 0.02, "out": 1.20},
    BRAIN_HEAVY: {"in": 1.00, "cached": 0.10, "out": 5.00},    # gpt-6.1-terra
    "gpt-5.6-terra": {"in": 2.00, "cached": 0.20, "out": 12.00},  # old, for history
    REALTIME:    {"in": 0.60, "cached": 0.06, "out": 2.40},
    VISION_FAST: {"in": 0.15, "cached": 0.075, "out": 0.60},   # gpt-4o-mini, vision
}

# Work that needs judgement rather than execution. Measured consequence of
# getting this wrong: every real request used to start on luna at LOW effort and
# needed two failures before reaching terra - three round trips to arrive at an
# answer the first call could have given.
HEAVY_WORDS = (
    "analyse", "analyze", "compare", "figure out", "work out", "diagnose",
    "debug", "fix", "refactor", "design", "decide", "why is", "why does",
    "troubleshoot", "investigate", "build", "create a", "write a", "script",
    "automate", "scrape", "extract", "summarise", "summarize", "review",
    "apply for", "fill in", "sign up", "reply to", "draft", "talk to",
    "keep talking", "until he", "until she", "watch", "monitor",
)

LIGHT_EXACT = {
    "yes", "no", "ok", "okay", "yeah", "yep", "nope", "sure", "thanks",
    "thank you", "hi", "hello", "hey", "stop", "wait", "cancel", "continue",
    "carry on", "go ahead", "do it", "again", "never mind",
}

MULTI = (" and then ", " then ", " after that ", " and also ", " until ")


def pick(text: str) -> tuple[str, str]:
    """(model, effort) for one request. Free: no network, no model call.

    A model call to decide which model to call would cost a round trip on every
    turn including "yes" - the entire latency saving, spent on the decision."""
    t = (text or "").strip().lower()
    if not t:
        return BRAIN, "low"
    if t.rstrip(".!?") in LIGHT_EXACT:
        return BRAIN, "low"
    if any(m in t for m in MULTI) or any(w in t for w in HEAVY_WORDS):
        # terra at MEDIUM, not high. Having paid 10x for the model, starving its
        # reasoning is the wrong economy - but terra-high was measured at ~30s
        # on a four-tool task, so it stays as the last resort it should be.
        return BRAIN_HEAVY, "medium"
    if len(t.split()) >= 25:
        return BRAIN_HEAVY, "medium"
    # Unsure takes the MIDDLE, never the floor. Starting at luna-low is what
    # made every real request fight its way up from the bottom.
    return BRAIN, "high"


def usd(model: str, input_tokens: int = 0, cached_tokens: int = 0,
        output_tokens: int = 0) -> float:
    p = PRICING.get(model)
    if not p:
        return 0.0
    fresh = max(0, input_tokens - cached_tokens)
    return round((fresh * p["in"] + cached_tokens * p["cached"]
                  + output_tokens * p["out"]) / 1_000_000, 6)


def record(resp, model: str) -> float:
    """Log one call's spend. Never raises - billing must not break a turn."""
    try:
        import state
        u = getattr(resp, "usage", None)
        if not u:
            return 0.0
        cached = getattr(getattr(u, "input_tokens_details", None),
                         "cached_tokens", 0) or 0
        cost = usd(model, u.input_tokens or 0, cached, u.output_tokens or 0)
        state.record_spend(model, u.input_tokens or 0, cached,
                           u.output_tokens or 0, cost)
        return cost
    except Exception:
        return 0.0
