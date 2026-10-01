"""
execution.py — run independent work at the same time, safely.

THE PROBLEM THIS SOLVES

    The brain already asks the model for several tool calls at once. It then
    ran them in a plain `for` loop, one after another. Four web lookups that
    take four seconds each took sixteen seconds, and nothing about them
    required an order.

    This module runs them together, and holds back only the ones that must not
    overlap.

WHY NOT JUST THREAD EVERYTHING

    Because two of these things are not like the others. Reading a file,
    fetching a URL and parsing JSON are independent - they touch nothing
    shared. Driving WhatsApp is not: it types into whichever chat is currently
    open, so two concurrent sends would interleave keystrokes into one
    conversation and deliver both to the wrong person.

    So every call declares the resource it needs, and the executor serialises
    anything that claims the same one. Independent work still runs at full
    width beside it.

HOW A RESOURCE IS DECIDED

    Statically, from the code the model wrote. `from lib import whatsapp` means
    it will drive WhatsApp; `import browser` means Chrome. That is a heuristic,
    and it is deliberately a PESSIMISTIC one: anything unrecognised is treated
    as exclusive, because being wrong in that direction costs a little speed,
    and being wrong in the other direction costs a message sent to the wrong
    person.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

MAX_WORKERS = 4                # more than this and the model is the bottleneck
CALL_TIMEOUT_S = 90


# ─────────────────────────────────────────────────────────────────────────────
# Resources
# ─────────────────────────────────────────────────────────────────────────────

FREE = "free"                  # touches nothing shared; run as wide as we like
DESKTOP = "desktop"            # WhatsApp, launching apps, media keys, clipboard
BROWSER = "browser"            # Chrome over CDP - one page, one thread
SHELL = "shell"                # subprocesses that change the machine

_LOCKS: dict[str, threading.Lock] = {
    DESKTOP: threading.Lock(),
    BROWSER: threading.Lock(),
    SHELL: threading.Lock(),
}

# Ordered: the first pattern that matches wins, so the most serialising
# resource is checked first.
_SIGNATURES = (
    (DESKTOP, re.compile(r"\b(whatsapp|desktop|open_app|media\(|clipboard)\b",
                         re.I)),
    (BROWSER, re.compile(r"\b(browser|playwright|chrome|goto\(|click_text|"
                         r"gmail_search)\b", re.I)),
    (SHELL, re.compile(r"\b(subprocess|os\.system|taskkill|powershell|"
                       r"shutil\.rmtree|send2trash)\b", re.I)),
)

# Anything that only reads is free, even though it imports something.
_READ_ONLY = re.compile(r"\b(files\.(read|ls|exists)|web\.(fetch|search)|"
                        r"state\.(facts|load|history|jobs|open_commitments)|"
                        r"recall)\b", re.I)


def resource_for(tool: str, args: dict) -> str:
    """Which exclusive resource this call needs, or FREE.

    Deliberately pessimistic about run_python: unrecognised code gets SHELL,
    so it serialises. A false FREE is how two WhatsApp sends end up interleaved
    in one chat."""
    if tool in ("recall", "ask_boss"):
        return FREE
    if tool == "schedule_job":
        return FREE                     # a row in SQLite; the worker does the work
    if tool != "run_python":
        return SHELL

    code = str(args.get("code") or "")
    for name, pattern in _SIGNATURES:
        if pattern.search(code):
            return name
    if _READ_ONLY.search(code):
        return FREE
    # Pure computation with no imports at all is safe to run wide.
    if not re.search(r"^\s*(import|from)\s", code, re.M):
        return FREE
    return SHELL


# ─────────────────────────────────────────────────────────────────────────────
# Running them
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Call:
    """One tool call the model asked for."""
    id: str
    tool: str
    args: dict
    resource: str = FREE
    result: dict | None = None
    seconds: float = 0.0


@dataclass
class Report:
    """What actually happened, for the metrics in §27 and for honesty."""
    wall_seconds: float = 0.0
    serial_seconds: float = 0.0
    calls: int = 0
    widest: int = 1
    by_resource: dict = field(default_factory=dict)

    @property
    def saved_seconds(self) -> float:
        """Time NOT spent, versus doing the same work one at a time."""
        return max(0.0, self.serial_seconds - self.wall_seconds)


def run_calls(calls: list[Call], dispatch, on_start=None) -> Report:
    """Execute every call, concurrently where it is safe to.

    `dispatch(tool, args) -> dict` is the brain's single gate; it is called
    from worker threads, so everything it touches must already be thread safe.
    It is - safety.Session counts are per-session and state.py opens a
    connection per call.

    Results are written back onto each Call in place, and the ORDER OF `calls`
    IS PRESERVED, so the brain can still match them to call ids."""
    for c in calls:
        c.resource = resource_for(c.tool, c.args)

    report = Report(calls=len(calls))
    report.by_resource = {}
    for c in calls:
        report.by_resource[c.resource] = report.by_resource.get(c.resource, 0) + 1

    started = time.perf_counter()
    if len(calls) == 1:
        c = calls[0]
        t0 = time.perf_counter()
        if on_start:
            on_start(c)
        c.result = dispatch(c.tool, c.args)
        c.seconds = time.perf_counter() - t0
        report.wall_seconds = c.seconds
        report.serial_seconds = c.seconds
        return report

    inflight = {"n": 0, "max": 1}
    counter_lock = threading.Lock()

    def work(c: Call) -> Call:
        lock = _LOCKS.get(c.resource)
        if lock is not None:
            lock.acquire()
        try:
            with counter_lock:
                inflight["n"] += 1
                inflight["max"] = max(inflight["max"], inflight["n"])
            t0 = time.perf_counter()
            if on_start:
                on_start(c)
            try:
                c.result = dispatch(c.tool, c.args)
            except Exception as e:          # a worker must never die silently
                c.result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            c.seconds = time.perf_counter() - t0
        finally:
            with counter_lock:
                inflight["n"] -= 1
            if lock is not None:
                lock.release()
        return c

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(calls)),
                            thread_name_prefix="exec") as pool:
        futures = [pool.submit(work, c) for c in calls]
        for f in as_completed(futures, timeout=CALL_TIMEOUT_S * len(calls)):
            try:
                f.result()
            except Exception as e:
                # work() already catches inside itself, so reaching here means
                # the pool machinery failed - rare, and exactly the kind of
                # thing that must not disappear.
                try:
                    import state
                    state.record_error("execution.run_calls", e, fatal=True)
                except Exception:
                    pass

    report.wall_seconds = time.perf_counter() - started
    report.serial_seconds = sum(c.seconds for c in calls)
    report.widest = inflight["max"]
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Dependency-aware execution
#
# run_calls() runs one batch as wide as its resources allow. That is enough
# when the model asks for four independent lookups, and not enough for real
# work, which usually looks like:
#
#     research A ─┐
#     research B ─┼─> compare ─> write the file
#     research C ─┘
#
# A/B/C are independent and must overlap; compare must not start until all
# three are in; the write must not start until compare is done. Running that
# as one flat batch would either serialise everything or start the compare on
# missing inputs, and both are wrong.
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Step:
    """One node. `needs` names the steps whose results this one requires."""
    id: str
    run: object                       # callable(results: dict) -> anything
    needs: tuple = ()
    resource: str = FREE


class CircularDependency(Exception):
    """A graph that can never start. Reported, not silently dropped."""


def run_graph(steps: list[Step], max_workers: int = MAX_WORKERS,
              on_event=None) -> dict:
    """Run a dependency graph as wide as its dependencies and resources allow.

    Returns {id: {ok, value, error, seconds}} for every step. A step whose
    dependency failed is NOT run and is reported as skipped with the reason -
    running it on missing inputs would produce a confident wrong answer, which
    is the failure mode this whole build exists to prevent."""
    by_id = {s.id: s for s in steps}
    if len(by_id) != len(steps):
        raise ValueError("two steps share an id")
    for s in steps:
        for need in s.needs:
            if need not in by_id:
                raise ValueError(f"step {s.id!r} needs unknown step {need!r}")

    # Refuse a cycle up front rather than deadlocking on it.
    pending = {s.id: set(s.needs) for s in steps}
    order: list[str] = []
    while pending:
        free = [i for i, n in pending.items() if not n]
        if not free:
            raise CircularDependency(
                f"these steps depend on each other: {sorted(pending)}")
        for i in free:
            order.append(i)
            pending.pop(i)
        for n in pending.values():
            n.difference_update(free)

    results: dict[str, dict] = {}
    done: set[str] = set()
    started = time.perf_counter()
    peak = {"n": 0, "cur": 0}
    guard = threading.Lock()

    def ready() -> list[Step]:
        return [by_id[i] for i in by_id
                if i not in results and i not in done
                and all(d in done for d in by_id[i].needs)]

    def execute(step: Step) -> tuple:
        lock = _LOCKS.get(step.resource)
        if lock is not None:
            lock.acquire()
        try:
            with guard:
                peak["cur"] += 1
                peak["n"] = max(peak["n"], peak["cur"])
            t0 = time.perf_counter()
            try:
                value = step.run({d: results[d].get("value")
                                  for d in step.needs})
                out = {"ok": True, "value": value}
            except Exception as e:
                out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            out["seconds"] = round(time.perf_counter() - t0, 3)
            return step.id, out
        finally:
            with guard:
                peak["cur"] -= 1
            if lock is not None:
                lock.release()

    with ThreadPoolExecutor(max_workers=max(1, max_workers),
                            thread_name_prefix="graph") as pool:
        inflight: dict = {}
        while True:
            for step in ready():
                if step.id in inflight:
                    continue
                if on_event:
                    on_event({"type": "step_start", "id": step.id})
                inflight[step.id] = pool.submit(execute, step)
            if not inflight:
                break
            for fut in as_completed(list(inflight.values())):
                sid, out = fut.result()
                results[sid] = out
                done.add(sid)
                inflight.pop(sid, None)
                if on_event:
                    on_event({"type": "step_done", "id": sid,
                              "ok": out["ok"]})
                # A failure cancels only what DEPENDED on it; unrelated
                # branches keep going, because losing them too would throw
                # away work that was never in question.
                if not out["ok"]:
                    for other in by_id.values():
                        if sid in other.needs and other.id not in results:
                            results[other.id] = {
                                "ok": False, "skipped": True, "seconds": 0.0,
                                "error": f"not run: {sid} failed"}
                            done.add(other.id)
                break

    for s in steps:
        results.setdefault(s.id, {"ok": False, "skipped": True,
                                  "seconds": 0.0,
                                  "error": "never became runnable"})
    return {"results": results,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "serial_seconds": round(sum(r.get("seconds", 0.0)
                                        for r in results.values()), 3),
            "peak_parallel": peak["n"],
            "order": order}
