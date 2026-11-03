"""
proactive.py — anticipation, bounded. Notice and PROPOSE; never act on your own.

A human assistant's defining trait is that it notices things nobody asked it to
watch - "your disk is nearly full", "that keeps failing" - and brings them up.
The build only ever reacted, or ran monitors the Boss explicitly scheduled. This
adds the anticipation, under the one rule that keeps it safe and keeps the owner's
"no self-initiated activity I did not ask for" intact:

    NOTICE and PROPOSE. Do not ACT on your own.

sweep() is READ-ONLY - it looks, it never changes anything and never messages.
notices() adds a per-item cooldown so a thing is flagged ONCE, not nagged. The
worker surfaces them to the Boss (respecting quiet hours and the rate limits he
set), and he decides. Nothing here deletes, spends, sends, or browses.

may_act() is the bounded-autonomy gate: on its own initiative Jarvis may act only
for a kind he has explicitly pre-authorised, and that list is EMPTY by default.
Everything else is a proposal. The gate exists so "bounded" is enforced in code,
not left to the prompt remembering to be careful.
"""
from __future__ import annotations

import time

import state

# Reversible, safe actions the owner has opted Jarvis into doing on its OWN -
# empty on purpose. Add a kind here only when he says "yes, do that without
# asking", e.g. {"archive_old_builds"}. Deletion and money can never be added.
AUTONOMOUS_WHITELIST: set[str] = set()

DISK_LOW_GB = 5.0
RELIABILITY_WINDOW_DAYS = 3
COOLDOWN_S = 6 * 3600                 # flag a thing at most this often
_SEEN_PREFIX = "_pro:"                # "_" => kept out of the prompt (facts_block)


def may_act(kind: str) -> bool:
    """True only for an action the owner pre-authorised Jarvis to take alone.
    Everything else must be proposed to him, not done."""
    return kind in AUTONOMOUS_WHITELIST


def sweep(now: float | None = None) -> list[dict]:
    """Read-only. Return things the Boss would want flagged, as PROPOSALS. Each
    check is guarded so one failing never silences the others, and nothing here
    changes the machine or reaches him - the caller decides what to surface."""
    now = now or time.time()
    out: list[dict] = []

    # Disk filling - propose moving, never deleting.
    try:
        from lib import desktop
        free = float(desktop.stats().get("disk_free_gb", 99))
        if free < DISK_LOW_GB:
            out.append({
                "key": "disk_low",
                "text": (f"Your disk is down to {free:.0f} GB free. Want me to "
                         f"move old builds to a holding folder? I won't delete "
                         f"anything."),
                "critical": free < 1.0})
    except Exception:
        pass

    # A run of failures - surface it so he knows something may be off.
    try:
        s = state.success_summary(days=RELIABILITY_WINDOW_DAYS)
        if s["rate"] is not None and s["failures"] >= 3 and s["rate"] < 0.5:
            out.append({
                "key": "reliability",
                "text": (f"Heads up - I've failed {s['failures']} tasks in the "
                         f"last few days ({s['rate']*100:.0f}% success). Something "
                         f"may be off and worth a look."),
                "critical": False})
    except Exception:
        pass

    return out


def notices(now: float | None = None, db_path=None) -> list[dict]:
    """sweep(), minus anything flagged within its cooldown, so a notice is raised
    once and not nagged. Marks what it returns as seen. Critical items bypass the
    cooldown. Still PROPOSALS - this does not message anyone."""
    now = now or time.time()
    try:
        facts = state.facts(db_path=db_path)
    except Exception:
        facts = {}
    fresh = []
    for n in sweep(now):
        key = _SEEN_PREFIX + n["key"]
        try:
            last = float(facts.get(key) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if n.get("critical") or (now - last) >= COOLDOWN_S:
            fresh.append(n)
            try:
                state.set_fact(key, f"{now:.0f}", db_path=db_path)
            except Exception:
                pass
    return fresh
