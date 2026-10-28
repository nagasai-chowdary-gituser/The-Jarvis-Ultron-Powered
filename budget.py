"""
budget.py — why a task stops. Never "because it did fifteen things".

WHAT WAS WRONG

    brain.run() was `for _ in range(MAX_ITERATIONS)` with MAX_ITERATIONS = 12,
    and safety.check() refused the 16th tool call. Falling out of that loop
    printed:

        "I ran out of steps before finishing that."

    Two numbers, one of them a plain `for` range, deciding whether a real
    objective was allowed to finish. Everything the agent might do - reasoning,
    a productive action, a retry after a failure, an observation, a recovery -
    was the same unit against the same counter, so THREE FAILED RETRIES COST
    EXACTLY AS MUCH AS THREE COMPLETED SUBGOALS. A task that hit an obstacle
    early was quietly rationed to death, and the message blamed the task's
    length for what was really an obstacle it was never given room to get past.

WHAT REPLACES IT

    A task runs while it is still getting somewhere, and stops for a REASON
    that is true about the work:

        COMPLETED   the objective was met and verified
        BLOCKED     something outside the agent makes it impossible; the
                    remedy is named
        CANCELLED   the owner said stop
        TIMEOUT     the wall-clock deadline passed
        NEEDS_AUTH  a high-risk action needs the PIN
        STUCK       no progress for long enough that more of the same is not
                    going to help
        RUNAWAY     a ceiling so high that reaching it means a bug, not a long
                    task

    RUNAWAY is the only survivor of the old model, and it is deliberately far
    out of the way of real work. It exists to stop an infinite loop burning
    money, not to decide whether an objective was worth finishing.

THE UNITS ARE KEPT SEPARATE

    reasoning iterations · productive actions · recovery attempts ·
    observations · elapsed time · consecutive no-progress rounds

    Collapsing those into one integer is the bug this file exists to undo.
    A recovery that rescues a task is not the same event as a step toward it,
    and must not be paid for out of the same purse.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    CONTINUE = "continue"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    NEEDS_AUTH = "needs_auth"
    STUCK = "stuck"
    RUNAWAY = "runaway"

    @property
    def terminal(self) -> bool:
        return self is not Verdict.CONTINUE

    @property
    def is_failure(self) -> bool:
        """BLOCKED and NEEDS_AUTH are not failures - they are answers that
        happen to require someone else. Reporting them as failure hides the ask."""
        return self in (Verdict.TIMEOUT, Verdict.STUCK, Verdict.RUNAWAY)


# ─────────────────────────────────────────────────────────────────────────────
# What counts as getting somewhere
#
# A tool call is not progress. A model call is not progress. Looking at
# something and finding it unchanged is not progress. Progress is the world
# being different, or the agent knowing something it did not know.
# ─────────────────────────────────────────────────────────────────────────────

PROGRESS_SIGNALS = {
    "artifact",      # a file, a directory, a message id - something now exists
    "state_change",  # a process started, a page navigated, playback changed
    "discovery",     # data the agent did not have before
    "subgoal",       # a named part of the objective finished
    "recovery",      # a failing path replaced by one that works
}


@dataclass
class Progress:
    """What has actually been achieved, and when.

    Signals are recorded by whoever observes them - the dispatcher when a tool
    returns something real, the verifier when an outcome checks out. This class
    only remembers; it does not guess."""
    signals: list[dict] = field(default_factory=list)
    last_at: float = field(default_factory=time.time)
    rounds_without: int = 0

    def record(self, kind: str, detail: str = "") -> bool:
        """Note a real advance. Returns whether it was accepted as progress."""
        if kind not in PROGRESS_SIGNALS:
            return False
        self.signals.append({"kind": kind, "detail": str(detail)[:200],
                             "at": time.time()})
        self.last_at = time.time()
        self.rounds_without = 0
        return True

    def barren_round(self) -> int:
        """A whole round finished and nothing changed. Returns the new streak."""
        self.rounds_without += 1
        return self.rounds_without

    @property
    def count(self) -> int:
        return len(self.signals)

    def seconds_since(self) -> float:
        return time.time() - self.last_at


# ─────────────────────────────────────────────────────────────────────────────
# Doing the same thing over and over
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LoopDetector:
    """Spot a repeat that is going nowhere.

    A repeat is only a loop when the OUTCOME repeats too. Clicking 'next' three
    times is three different things happening; running the same failing command
    three times with the same error is one thing happening three times."""
    limit: int = 3
    seen: dict = field(default_factory=dict)
    loops: list[str] = field(default_factory=list)

    @staticmethod
    def fingerprint(tool: str, args: dict, outcome: str = "") -> str:
        raw = f"{tool}|{sorted((args or {}).items(), key=lambda kv: kv[0])}|{outcome}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:16]

    def note(self, tool: str, args: dict, outcome: str = "") -> int:
        fp = self.fingerprint(tool, args, outcome)
        self.seen[fp] = self.seen.get(fp, 0) + 1
        n = self.seen[fp]
        if n == self.limit and fp not in self.loops:
            self.loops.append(fp)
        return n

    def looping(self, tool: str, args: dict, outcome: str = "") -> bool:
        return self.seen.get(self.fingerprint(tool, args, outcome), 0) >= self.limit

    @property
    def any_loop(self) -> bool:
        return bool(self.loops)


# ─────────────────────────────────────────────────────────────────────────────
# The governor
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Budget:
    """Several independent constraints. None of them is "number of steps".

    Defaults are sized for real work rather than for a demo: five minutes and
    forty reasoning rounds is far more than any sane task needs, and the task
    is expected to finish long before any of it on a terminal condition of its
    own. They are a backstop, not a schedule."""

    deadline_s: float = 300.0          # wall clock for the whole objective
    max_barren_rounds: int = 4         # rounds in a row achieving nothing
    recovery_budget: int = 8           # retries/alternatives, SEPARATE purse
    runaway_rounds: int = 40           # a bug, not a long task
    runaway_actions: int = 300

    started: float = field(default_factory=time.time)
    rounds: int = 0                    # reasoning iterations
    actions: int = 0                   # productive tool calls
    recoveries: int = 0                # retries and alternative strategies
    observations: int = 0              # looks that changed nothing

    progress: Progress = field(default_factory=Progress)
    loops: LoopDetector = field(default_factory=LoopDetector)

    cancelled: bool = False
    blocked_reason: str = ""
    needs_auth: str = ""
    task_id: int | None = None

    # ── recording what happened ──────────────────────────────────────────────

    def round_started(self) -> None:
        self.rounds += 1

    def did(self, productive: bool = True) -> None:
        if productive:
            self.actions += 1
        else:
            self.observations += 1

    def recovered(self) -> None:
        """A retry or an alternative strategy. Its own purse, deliberately:
        three failed retries used to cost the same as three finished subgoals."""
        self.recoveries += 1

    def block(self, reason: str) -> None:
        self.blocked_reason = reason

    def require_auth(self, what: str) -> None:
        self.needs_auth = what

    def cancel(self) -> None:
        self.cancelled = True

    # ── the decision ─────────────────────────────────────────────────────────

    def verdict(self) -> Verdict:
        """Should this task keep going? Checked between rounds.

        Order matters. Cancellation outranks everything because it is the
        owner's word; a blocker outranks a deadline because the reason the
        task is stopping should be the TRUE one, not merely the first timer
        to expire."""
        if self.cancelled:
            return Verdict.CANCELLED
        if self.task_id is not None:
            try:
                import tasks
                if tasks.cancel_requested(self.task_id):
                    self.cancelled = True
                    return Verdict.CANCELLED
            except Exception:
                pass
        if self.needs_auth:
            return Verdict.NEEDS_AUTH
        if self.blocked_reason:
            return Verdict.BLOCKED
        if self.elapsed >= self.deadline_s:
            return Verdict.TIMEOUT
        if self.recoveries > self.recovery_budget:
            return Verdict.STUCK
        if self.progress.rounds_without >= self.max_barren_rounds:
            return Verdict.STUCK
        if self.rounds >= self.runaway_rounds or self.actions >= self.runaway_actions:
            return Verdict.RUNAWAY
        return Verdict.CONTINUE

    @property
    def elapsed(self) -> float:
        return time.time() - self.started

    @property
    def seconds_left(self) -> float:
        return max(0.0, self.deadline_s - self.elapsed)

    def explain(self, verdict: Verdict) -> str:
        """Say why in words that are true and useful to a person.

        Notably there is no wording here for running out of steps, because
        that is no longer a thing that can happen."""
        if verdict is Verdict.CANCELLED:
            return "You stopped it, so I stopped."
        if verdict is Verdict.NEEDS_AUTH:
            return (f"That needs your PIN first: {self.needs_auth}. "
                    f"Give it to me and I will carry on.")
        if verdict is Verdict.BLOCKED:
            return self.blocked_reason
        if verdict is Verdict.TIMEOUT:
            return (f"That took the full time I had for it ({self.deadline_s:.0f}s) "
                    f"- I got {self.progress.count} step(s) done. Here is how far "
                    f"I got; say the word and I'll keep going.")
        if verdict is Verdict.STUCK:
            if self.recoveries > self.recovery_budget:
                return (f"I tried {self.recoveries} different ways and none of "
                        f"them moved this forward, so I have stopped rather "
                        f"than keep burning time.")
            return (f"Nothing I tried in the last {self.progress.rounds_without} "
                    f"rounds changed anything, so carrying on the same way "
                    f"would not help.")
        if verdict is Verdict.RUNAWAY:
            return ("I hit my safety ceiling, which means something looped. "
                    "That is a bug on my side, not a limit on the task.")
        return ""

    def snapshot(self) -> dict:
        """Everything measured, for the metrics and for an honest report."""
        return {"rounds": self.rounds, "actions": self.actions,
                "recoveries": self.recoveries, "observations": self.observations,
                "progress": self.progress.count,
                "barren_rounds": self.progress.rounds_without,
                "loops_detected": len(self.loops.loops),
                "elapsed": round(self.elapsed, 2),
                "seconds_left": round(self.seconds_left, 2)}


def for_request(text: str, task_id: int | None = None) -> Budget:
    """Size the budget to the request. Simple things stay cheap.

    "Open Notepad" gets a small deadline and a low ceiling, so it cannot turn
    into a planning festival. Anything that reads like real work gets room.
    This is sizing, not gating: exceeding a small budget on a simple task is
    itself a signal that the request was not what it looked like."""
    t = (text or "").lower()
    words = len(t.split())

    heavy = any(w in t for w in (
        "research", "compare", "organise", "organize", "every", "all of",
        "then", "after that", "and also", "until", "fix", "debug",
        "investigate", "summarise", "summarize", "download", "queue",
        # Creative / on-screen DOING is slow (app launch, drawing, the cursor),
        # so it must not be rationed to the chat-sized budget - a "draw me a
        # smiley" that opened Paint used to hit the 60s limit.
        "draw", "paint", "sketch", "design", "create", "make me", "make a",
        "picture", "image", "build", "write a", "express"))

    if words <= 6 and not heavy:
        return Budget(deadline_s=60.0, max_barren_rounds=3,
                      recovery_budget=4, runaway_rounds=12,
                      runaway_actions=40, task_id=task_id)
    if heavy or words > 25:
        return Budget(deadline_s=420.0, max_barren_rounds=5,
                      recovery_budget=12, runaway_rounds=60,
                      runaway_actions=400, task_id=task_id)
    return Budget(task_id=task_id)
