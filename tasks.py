"""
tasks.py — the single authority for what Jarvis is doing and how far it got.

WHY THIS EXISTS

    Before this file there was no task. `jobs` in SQLite had four statuses -
    pending, done, failed, expired - and that was the whole model. Nothing
    could be cancelled, nothing could be paused, nothing recorded which
    attempt it was on or what had already been tried, and a run that died
    mid-flight left a row that said `pending` forever with no way to tell it
    apart from one that had never started.

    So "stop" was a word Jarvis could say and not a thing it could do.

WHAT A TASK IS HERE

    One objective, one lifecycle, one row, one owner. Every transition is
    written to disk as it happens, so the state on restart is the state that
    was actually reached rather than the state that was last intended.

        CREATED -> PLANNING -> EXECUTING -> OBSERVING -> VERIFYING -> COMPLETED
                                  |  ^                       |
                                  v  |                       v
                             RECOVERING                   FAILED
                                  |
                          PAUSED / CANCELLED / ESCALATED

    FAILED, COMPLETED, CANCELLED and ESCALATED are terminal. ESCALATED is
    deliberately NOT a failure: it means the machine did everything it could
    and a human is genuinely required, which is a different thing from the
    work being impossible, and reporting it as failure would hide the ask.

HOW CANCELLATION ACTUALLY STOPS SOMETHING

    Cooperatively, and therefore honestly. A flag is written to the row and
    mirrored to an in-process Event; long work calls `checkpoint()` between
    steps and raises `Cancelled` when the flag is set. There is no thread
    killing here, because killing a thread mid-write is how a half-written
    file and a corrupted database happen - the work stops at a line where
    stopping is safe, and the row records that it stopped there.

    That does mean a step already inside a 60-second subprocess finishes that
    subprocess. `cancel()` therefore also terminates the process group the
    task registered, so the wait is bounded by the step rather than unbounded.
"""
from __future__ import annotations

import json
import threading
import time
from enum import Enum
from pathlib import Path

import state

# ─────────────────────────────────────────────────────────────────────────────
# The states
# ─────────────────────────────────────────────────────────────────────────────


class State(str, Enum):
    CREATED = "created"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"

    @property
    def terminal(self) -> bool:
        return self in (State.COMPLETED, State.FAILED, State.CANCELLED,
                        State.ESCALATED)

    @property
    def running(self) -> bool:
        return self in (State.PLANNING, State.EXECUTING, State.OBSERVING,
                        State.VERIFYING, State.RECOVERING)


# Which moves are legal. Written down so an illegal one is a loud error rather
# than a row that quietly claims something impossible - COMPLETED after
# CANCELLED was exactly the kind of thing that used to be representable.
ALLOWED: dict[State, set[State]] = {
    State.CREATED:    {State.PLANNING, State.CANCELLED, State.FAILED},
    State.PLANNING:   {State.EXECUTING, State.CANCELLED, State.FAILED,
                       State.ESCALATED, State.PAUSED},
    State.EXECUTING:  {State.OBSERVING, State.RECOVERING, State.CANCELLED,
                       State.FAILED, State.PAUSED, State.ESCALATED},
    State.OBSERVING:  {State.VERIFYING, State.EXECUTING, State.RECOVERING,
                       State.CANCELLED, State.FAILED, State.PAUSED},
    State.VERIFYING:  {State.COMPLETED, State.RECOVERING, State.FAILED,
                       State.CANCELLED, State.ESCALATED},
    # RECOVERING -> VERIFYING is legal, and leaving it out was a bug the
    # verification check caught: a task whose outcome did not hold went to
    # RECOVERING, was fixed, and then had no legal way to reach COMPLETED.
    # Recovery that cannot lead back to success is just a nicer word for
    # failure.
    State.RECOVERING: {State.PLANNING, State.EXECUTING, State.VERIFYING,
                       State.FAILED, State.CANCELLED, State.ESCALATED,
                       State.PAUSED},
    State.PAUSED:     {State.EXECUTING, State.PLANNING, State.CANCELLED,
                       State.FAILED},
    State.COMPLETED:  set(),
    State.FAILED:     set(),
    State.CANCELLED:  set(),
    State.ESCALATED:  set(),
}

MAX_ATTEMPTS = 3
STALE_AFTER_S = 15 * 60      # a running row untouched this long was interrupted


class Cancelled(Exception):
    """Raised at a checkpoint when the owner asked for this task to stop."""


class IllegalTransition(Exception):
    """A move the state machine does not allow. A bug, surfaced not hidden."""


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    objective   TEXT NOT NULL,
    state       TEXT NOT NULL,
    transport   TEXT,
    action      TEXT,
    result      TEXT,
    error       TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    tried       TEXT,                      -- JSON list of strategies attempted
    cancel_req  INTEGER NOT NULL DEFAULT 0,
    pause_req   INTEGER NOT NULL DEFAULT 0,
    pid         INTEGER,                   -- current child process, if any
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state, updated_at);
CREATE TABLE IF NOT EXISTS task_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    ts      REAL NOT NULL,
    frm     TEXT,
    to_     TEXT NOT NULL,
    note    TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, id);
"""

_ready = False
_lock = threading.RLock()
_stop_events: dict[int, threading.Event] = {}


def _conn(db_path: Path | None = None):
    global _ready
    c = state._conn(db_path)
    if not _ready:
        c.executescript(SCHEMA)
        c.commit()
        _ready = True
    return c


def reset_for_tests() -> None:
    """Let a test point at a fresh database. Schema is re-applied on next use."""
    global _ready
    _ready = False
    _stop_events.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

def create(objective: str, transport: str = "desk",
           db_path: Path | None = None) -> int:
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("a task needs an objective")
    now = time.time()
    c = _conn(db_path)
    try:
        cur = c.execute(
            "INSERT INTO tasks (objective, state, transport, attempts, tried,"
            " created_at, updated_at) VALUES (?,?,?,0,'[]',?,?)",
            (state.redact(objective)[:2000], State.CREATED.value, transport,
             now, now))
        tid = int(cur.lastrowid or 0)
        c.execute("INSERT INTO task_events (task_id, ts, frm, to_, note)"
                  " VALUES (?,?,?,?,?)",
                  (tid, now, None, State.CREATED.value, "created"))
        c.commit()
    finally:
        c.close()
    _stop_events[tid] = threading.Event()
    return tid


def get(task_id: int, db_path: Path | None = None) -> dict | None:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    finally:
        c.close()
    if not r:
        return None
    d = dict(r)
    d["state"] = State(d["state"])
    try:
        d["tried"] = json.loads(d.get("tried") or "[]")
    except Exception:
        d["tried"] = []
    return d


def history(task_id: int, db_path: Path | None = None) -> list[dict]:
    """Every transition this task actually made, oldest first."""
    c = _conn(db_path)
    try:
        rows = c.execute("SELECT ts, frm, to_, note FROM task_events"
                         " WHERE task_id=? ORDER BY id", (task_id,)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def move(task_id: int, to: State, note: str = "", action: str = "",
         result: str = "", error: str = "",
         db_path: Path | None = None) -> dict:
    """Transition a task, refusing illegal moves and persisting immediately."""
    with _lock:
        t = get(task_id, db_path)
        if t is None:
            raise ValueError(f"no task {task_id}")
        frm = t["state"]
        if frm.terminal:
            raise IllegalTransition(
                f"task {task_id} is already {frm.value}; it cannot become "
                f"{to.value}")
        if to not in ALLOWED[frm]:
            raise IllegalTransition(
                f"task {task_id}: {frm.value} -> {to.value} is not allowed")

        now = time.time()
        c = _conn(db_path)
        try:
            c.execute(
                "UPDATE tasks SET state=?, updated_at=?,"
                " action=COALESCE(NULLIF(?,''), action),"
                " result=COALESCE(NULLIF(?,''), result),"
                " error=COALESCE(NULLIF(?,''), error) WHERE id=?",
                (to.value, now, state.redact(action)[:500],
                 state.redact(result)[:4000], state.redact(error)[:2000],
                 task_id))
            c.execute("INSERT INTO task_events (task_id, ts, frm, to_, note)"
                      " VALUES (?,?,?,?,?)",
                      (task_id, now, frm.value, to.value,
                       state.redact(note)[:500]))
            c.commit()
        finally:
            c.close()
        return get(task_id, db_path)


def note_attempt(task_id: int, strategy: str,
                 db_path: Path | None = None) -> int:
    """Record that a strategy was tried. Returns the new attempt count.

    The list of what has ALREADY been tried is the point: bounded retries are
    not enough on their own, because three identical attempts are still one
    idea. Recovery reads this to pick something different."""
    with _lock:
        t = get(task_id, db_path)
        if t is None:
            raise ValueError(f"no task {task_id}")
        tried = list(t["tried"])
        tried.append(strategy[:200])
        n = int(t["attempts"]) + 1
        c = _conn(db_path)
        try:
            c.execute("UPDATE tasks SET attempts=?, tried=?, updated_at=?"
                      " WHERE id=?", (n, json.dumps(tried[-20:]), time.time(),
                                      task_id))
            c.commit()
        finally:
            c.close()
        return n


def already_tried(task_id: int, strategy: str,
                  db_path: Path | None = None) -> bool:
    t = get(task_id, db_path)
    return bool(t and strategy[:200] in t["tried"])


# ─────────────────────────────────────────────────────────────────────────────
# Stopping
# ─────────────────────────────────────────────────────────────────────────────

def cancel(task_id: int, reason: str = "", db_path: Path | None = None) -> dict:
    """Ask a task to stop, and make the ask visible everywhere at once.

    The flag goes to disk (so another process sees it) AND to an in-process
    Event (so a thread already running sees it without polling the database).
    Any child process the task registered is terminated, so a step sitting in
    a long subprocess does not hold the cancellation until it finishes."""
    with _lock:
        t = get(task_id, db_path)
        if t is None:
            raise ValueError(f"no task {task_id}")
        c = _conn(db_path)
        try:
            c.execute("UPDATE tasks SET cancel_req=1, updated_at=? WHERE id=?",
                      (time.time(), task_id))
            c.commit()
        finally:
            c.close()
        ev = _stop_events.setdefault(task_id, threading.Event())
        ev.set()
        _kill_child(t.get("pid"))
        if t["state"].terminal:
            return t
        return move(task_id, State.CANCELLED, note=reason or "cancelled",
                    db_path=db_path)


def pause(task_id: int, db_path: Path | None = None) -> dict:
    with _lock:
        c = _conn(db_path)
        try:
            c.execute("UPDATE tasks SET pause_req=1, updated_at=? WHERE id=?",
                      (time.time(), task_id))
            c.commit()
        finally:
            c.close()
        t = get(task_id, db_path)
        if t and t["state"].running:
            return move(task_id, State.PAUSED, note="paused", db_path=db_path)
        return t


def resume(task_id: int, db_path: Path | None = None) -> dict:
    """Continue a paused task from where it stopped, not from the beginning."""
    with _lock:
        t = get(task_id, db_path)
        if t is None:
            raise ValueError(f"no task {task_id}")
        if t["state"] is not State.PAUSED:
            return t
        c = _conn(db_path)
        try:
            c.execute("UPDATE tasks SET pause_req=0, updated_at=? WHERE id=?",
                      (time.time(), task_id))
            c.commit()
        finally:
            c.close()
        return move(task_id, State.EXECUTING, note="resumed", db_path=db_path)


def cancel_requested(task_id: int, db_path: Path | None = None) -> bool:
    ev = _stop_events.get(task_id)
    if ev is not None and ev.is_set():
        return True
    t = get(task_id, db_path)
    return bool(t and t["cancel_req"])


def pause_requested(task_id: int, db_path: Path | None = None) -> bool:
    t = get(task_id, db_path)
    return bool(t and t["pause_req"])


def checkpoint(task_id: int, db_path: Path | None = None) -> None:
    """Call between steps. Raises Cancelled if the owner asked to stop.

    Long work that never calls this cannot be stopped, which is why the
    executors call it around every step rather than once at the top."""
    if cancel_requested(task_id, db_path):
        raise Cancelled(f"task {task_id} was cancelled")


def register_child(task_id: int, pid: int | None,
                   db_path: Path | None = None) -> None:
    """Tell the task which process it is currently waiting on, so cancelling
    it can end that wait instead of blocking on it."""
    c = _conn(db_path)
    try:
        c.execute("UPDATE tasks SET pid=?, updated_at=? WHERE id=?",
                  (pid, time.time(), task_id))
        c.commit()
    finally:
        c.close()


def _kill_child(pid) -> bool:
    if not pid:
        return False
    try:
        import psutil
        p = psutil.Process(int(pid))
        for kid in p.children(recursive=True):
            try:
                kid.kill()
            except Exception:
                pass
        p.kill()
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Recovery after an interruption
# ─────────────────────────────────────────────────────────────────────────────

def running(db_path: Path | None = None) -> list[dict]:
    c = _conn(db_path)
    try:
        rows = c.execute(
            "SELECT * FROM tasks WHERE state IN (?,?,?,?,?) ORDER BY id",
            tuple(s.value for s in (State.PLANNING, State.EXECUTING,
                                    State.OBSERVING, State.VERIFYING,
                                    State.RECOVERING))).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["state"] = State(d["state"])
        out.append(d)
    return out


def recover_interrupted(stale_after_s: float = STALE_AFTER_S,
                        db_path: Path | None = None) -> list[int]:
    """Deal with rows left mid-flight by a crash or a kill.

    A process that dies during EXECUTING leaves a row claiming it is still
    executing, and nothing would ever move it again - so the task was both
    not running and not finished, forever. On startup those rows are found by
    age and either handed back for another attempt or failed honestly once
    they have used their attempts.

    IT MUST BE IDEMPOTENT, AND IT WAS NOT. `running()` includes RECOVERING,
    and this moved everything it returned INTO RECOVERING - so a task already
    in that state got `recovering -> recovering`, which the transition map
    forbids outright, because a self-transition is not a transition. The
    exception came out of a sweep that runs before the server starts, so it
    did not merely skip one row:

        tasks.IllegalTransition: task 5: recovering -> recovering

    killed the whole process on boot. And it was PERMANENT. A crash during
    recovery leaves exactly that row, and every later start hit the same line
    and died the same way - no HTTP server, no Telegram, no tunnel, no phone.
    The more Jarvis crashed, the more certainly it could never start again.

    A crash can land at any instant, recovery included, so finding a task
    already in the state this wants to set is a NORMAL observation, not an
    error. It is already marked for pickup; it needs no transition, and it is
    still returned as revived because it is still waiting for someone.

    One bad row also stops being able to hide the others: each is handled on
    its own, and a row that cannot be moved is recorded and stepped over."""
    now = time.time()
    revived: list[int] = []
    for t in running(db_path):
        if now - float(t["updated_at"] or 0) < stale_after_s:
            continue                      # genuinely still being worked on
        tid = int(t["id"])
        try:
            if int(t["attempts"]) >= MAX_ATTEMPTS:
                move(tid, State.FAILED,
                     note="interrupted and out of attempts",
                     error="the process running this task stopped and it had "
                           "already used every attempt",
                     db_path=db_path)
                continue
            # A RESTART IS AN ATTEMPT, and not counting it made the check
            # above dead code. Nothing anywhere consumes a RECOVERING task, so
            # a row moved here by a restart was never picked up by anybody: it
            # stayed non-terminal for good, was counted as running for good,
            # and was re-announced as "revived" on every single boot with
            # attempts still sitting at 0. Immortal is not better than failed.
            # Counting the restart makes MAX_ATTEMPTS mean something, so a task
            # nothing is going to resume dies honestly on the third try.
            note_attempt(tid, "restart after interruption", db_path=db_path)
            if t["state"] is not State.RECOVERING:
                move(tid, State.RECOVERING,
                     note="interrupted; picked up again after restart",
                     db_path=db_path)
            revived.append(tid)
        except Exception as e:
            # Never fatal. This runs before the server does, and a task row
            # nobody can explain must not be able to keep the phone offline.
            state.record_error(f"tasks.recover_interrupted[{tid}]", e)
    return revived


def sweep_stop_events() -> None:
    """Drop Events for tasks that have finished, so the map cannot grow forever."""
    for tid in list(_stop_events):
        t = get(tid)
        if t is None or t["state"].terminal:
            _stop_events.pop(tid, None)


# ─────────────────────────────────────────────────────────────────────────────
# Knowing when to stop trying
# ─────────────────────────────────────────────────────────────────────────────

def next_strategy(task_id: int, options: list[str],
                  db_path: Path | None = None) -> str | None:
    """The first option that has NOT been tried yet, or None if all are spent.

    Bounded retries alone do not help if all three attempts are the same idea.
    This makes "try something different" mechanical rather than hopeful: the
    caller lists the ways it knows, and gets back one it has not used."""
    t = get(task_id, db_path)
    if t is None:
        return None
    tried = set(t["tried"])
    for option in options:
        if option[:200] not in tried:
            return option
    return None


def escalate(task_id: int, need: str, done_so_far: str = "",
             db_path: Path | None = None) -> dict:
    """Stop and ask the owner, saying exactly what is required.

    ESCALATED is not FAILED. The distinction matters to the person reading it:
    failure means the thing could not be done, escalation means the machine
    did its part and one specific human action is left. Reporting the second
    as the first hides the ask, and the work then sits there forever."""
    if not (need or "").strip():
        raise ValueError("escalation must say what is actually needed")
    return move(task_id, State.ESCALATED,
                note=f"needs the owner: {need}",
                result=done_so_far, error="", db_path=db_path)


def out_of_attempts(task_id: int, db_path: Path | None = None) -> bool:
    t = get(task_id, db_path)
    return bool(t and int(t["attempts"]) >= MAX_ATTEMPTS)
