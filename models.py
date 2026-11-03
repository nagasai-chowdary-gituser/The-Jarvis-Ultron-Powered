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

import os

# EVERY ID IS ENV-OVERRIDABLE, DEFAULTS BELOW. The owner runs gpt-6.1-terra as
# the intelligent brain and gpt-4o-mini as the eyes; a model rename is then a
# line in .env, never a code edit (and never a redeploy). The defaults are what
# he asked for; JARVIS_BRAIN / JARVIS_BRAIN_HEAVY / JARVIS_VISION override them.
#
# "Balanced" dial (his choice): trivial turns stay on the cheap fast model,
# real work runs on the strong one with reasoning bought. See pick().
BRAIN = os.environ.get("JARVIS_BRAIN") or "gpt-5.6-luna"        # cheap + fast
BRAIN_HEAVY = os.environ.get("JARVIS_BRAIN_HEAVY") or "gpt-6.1-terra"  # the real brain
REALTIME = os.environ.get("JARVIS_REALTIME") or "gpt-realtime-mini"   # voice
VISION = os.environ.get("JARVIS_VISION") or "gpt-4o-mini"      # the eyes

MAX_OUTPUT_TOKENS = 4000
DAILY_CAP_USD = float(os.environ.get("JARVIS_DAILY_CAP_USD") or 8.0)

# Keyed by LITERAL id, not by the variables above, so an env override cannot
# empty the table and silently zero out cost logging. A model missing here bills
# as 0.0 (usd() fails safe) - add its row when you add the model.
# The gpt-6.1-terra row is a PLACEHOLDER: verify against the pricing page, since
# GET /v1/models returns ids but no rates (see AGENTS.md deviations).
PRICING = {
    "gpt-5.6-luna":      {"in": 0.20, "cached": 0.02, "out": 1.20},
    "gpt-5.6-terra":     {"in": 2.00, "cached": 0.20, "out": 12.00},
    "gpt-6.1-terra":     {"in": 2.50, "cached": 0.25, "out": 15.00},  # VERIFY
    "gpt-4o-mini":       {"in": 0.15, "cached": 0.075, "out": 0.60},
    "gpt-realtime-mini": {"in": 0.60, "cached": 0.06, "out": 2.40},
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
