"""
state.py — everything Jarvis remembers. One SQLite file, six tables.

REPLACES: memory.py (416), commitments.py (280), tasks.py (273), cost.py (341).
Four files, 1,310 lines, four connection helpers, four migration schemes. They
were separate because each was written on a different day to fix a different
symptom, not because they were different problems.

THE BUG THIS EXISTS TO KILL. From the boss's own Telegram log, sixty seconds
apart:

    [phone]    "Chitti Babu says to call you and keep a conference call"
    [telegram] "His latest message doesn't ask for a call, so I didn't place one"

Nothing was broken in the reading. The second turn re-read the chat from
scratch, formed a fresh opinion, and had no idea the first turn had ever
happened. Every turn started blind.

`open_intents` is the fix, and it is a column, not a prompt instruction. "He
asked for a call" is written down when it is observed and stays written down
until something resolves it. A later turn loads it and cannot contradict it,
because the contradiction would have to be an explicit overwrite rather than an
accident of forgetting.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent

# JARVIS_DB lets a subprocess be pointed at the same database as its parent.
# run_python runs in a CHILD process, which imports this module fresh - so a
# test that redirected DB_PATH in the parent had the agent faithfully writing
# to the real database while the test read an empty temporary one, and then
# reported a perfectly correct agent as having lost the fact. An environment
# variable is inherited; a module attribute is not.
DB_PATH = Path(os.environ.get("JARVIS_DB") or (ROOT / "data" / "jarvis.db"))

# How much history a turn carries. Enough that a reference back resolves;
# bounded so a long relationship does not become the bill.
CONTEXT_MESSAGES = 20
KEEP_MESSAGES = 200          # per contact, rolling

_SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    jid     TEXT PRIMARY KEY,
    name    TEXT,
    aliases TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    jid       TEXT NOT NULL,
    direction TEXT NOT NULL,          -- in | out
    text      TEXT NOT NULL,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_jid ON messages(jid, id);
CREATE TABLE IF NOT EXISTS threads (
    jid          TEXT PRIMARY KEY,
    summary      TEXT,
    open_intents TEXT,                -- JSON list; the anti-contradiction column
    updated_at   REAL
);
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    params      TEXT,                 -- JSON
    every_seconds INTEGER,
    until       TEXT,
    next_run_at REAL,
    status      TEXT NOT NULL DEFAULT 'pending',
    result      TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, next_run_at);
CREATE TABLE IF NOT EXISTS commitments (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    who     TEXT,
    what    TEXT NOT NULL,
    due     TEXT,
    source  TEXT,
    status  TEXT NOT NULL DEFAULT 'open',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS spend (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    day    TEXT NOT NULL,
    model  TEXT,
    input_tokens INTEGER DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    usd    REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_spend_day ON spend(day);
CREATE TABLE IF NOT EXISTS outreach (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    day      TEXT NOT NULL,
    kind     TEXT NOT NULL,           -- telegram | call
    job_id   INTEGER,                 -- which job fired it, or NULL if a person did
    reason   TEXT,
    critical INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outreach_day ON outreach(day, kind);
"""

_ready: set[str] = set()
_lock = threading.Lock()


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    """One connection helper, not four.

    The schema runs ONCE per database per process. Running it on every
    connection means a schema write - an exclusive lock - on every read, and
    with a worker polling every ten seconds that is a queue for no reason."""
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=15)
    c.row_factory = sqlite3.Row
    key = str(path)
    if key not in _ready:
        with _lock:
            if key not in _ready:
                c.executescript(_SCHEMA)
                _ready.add(key)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Secrets never get written down
# ─────────────────────────────────────────────────────────────────────────────

def redact(text: str) -> str:
    """Strip the PIN out of anything before it is stored.

    Kept from the old build because it caught a real leak: the boss spoke his
    PIN on a call, it was transcribed, and it sat in the turns table in plain
    text where it was read back into every later prompt. The PIN gate is gone
    now, but JARVIS_PIN may still be set, and a secret in a database is a
    secret in a database."""
    pin = (os.environ.get("JARVIS_PIN") or "").strip()
    if not pin or len(pin) < 3 or not pin.isdigit():
        return text or ""
    return re.sub(r"\b" + r"[\s\-.]*".join(pin) + r"\b", "[PIN]", text or "")


# ─────────────────────────────────────────────────────────────────────────────
# Contacts
# ─────────────────────────────────────────────────────────────────────────────

def upsert_contact(jid: str, name: str = "", aliases: list[str] | None = None,
                   db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        c.execute(
            "INSERT INTO contacts (jid, name, aliases) VALUES (?,?,?) "
            "ON CONFLICT(jid) DO UPDATE SET name=COALESCE(NULLIF(excluded.name,''), name),"
            " aliases=COALESCE(excluded.aliases, aliases)",
            (jid, name, json.dumps(aliases or [])))
        c.commit()
    finally:
        c.close()


def find_contact(needle: str, db_path: Path | None = None) -> dict | None:
    """Fuzzy name -> contact row. The JID is the answer, never a position."""
    n = (needle or "").strip().lower()
    if not n:
        return None
    c = _conn(db_path)
    try:
        rows = c.execute("SELECT * FROM contacts").fetchall()
    finally:
        c.close()
    for r in rows:                       # exact jid or exact name first
        if n in (r["jid"].lower(), (r["name"] or "").lower()):
            return dict(r)
    for r in rows:                       # then substring, then aliases
        if n in (r["name"] or "").lower():
            return dict(r)
    for r in rows:
        try:
            if any(n in a.lower() for a in json.loads(r["aliases"] or "[]")):
                return dict(r)
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Messages and the thread
# ─────────────────────────────────────────────────────────────────────────────

def add_message(jid: str, direction: str, text: str,
                db_path: Path | None = None) -> None:
    text = redact((text or "").strip())
    if not text:
        return
    c = _conn(db_path)
    try:
        c.execute("INSERT INTO messages (jid, direction, text, ts) VALUES (?,?,?,?)",
                  (jid, direction, text[:8000], time.time()))
        # Rolling window: keep the newest KEEP_MESSAGES per contact.
        c.execute(
            "DELETE FROM messages WHERE jid=? AND id NOT IN "
            "(SELECT id FROM messages WHERE jid=? ORDER BY id DESC LIMIT ?)",
            (jid, jid, KEEP_MESSAGES))
        c.commit()
    finally:
        c.close()


def history(jid: str, n: int = CONTEXT_MESSAGES,
            db_path: Path | None = None) -> list[dict]:
    c = _conn(db_path)
    try:
        rows = c.execute(
            "SELECT direction, text, ts FROM messages WHERE jid=?"
            " ORDER BY id DESC LIMIT ?", (jid, n)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows][::-1]


def thread(jid: str, db_path: Path | None = None) -> dict:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT * FROM threads WHERE jid=?", (jid,)).fetchone()
    finally:
        c.close()
    if not r:
        return {"jid": jid, "summary": "", "open_intents": [], "updated_at": 0}
    try:
        intents = json.loads(r["open_intents"] or "[]")
    except Exception:
        intents = []
    return {"jid": jid, "summary": r["summary"] or "",
            "open_intents": intents, "updated_at": r["updated_at"] or 0}


def update_thread(jid: str, summary: str | None = None,
                  open_intents: list[str] | None = None,
                  db_path: Path | None = None) -> None:
    cur = thread(jid, db_path=db_path)
    c = _conn(db_path)
    try:
        c.execute(
            "INSERT INTO threads (jid, summary, open_intents, updated_at)"
            " VALUES (?,?,?,?) ON CONFLICT(jid) DO UPDATE SET"
            " summary=excluded.summary, open_intents=excluded.open_intents,"
            " updated_at=excluded.updated_at",
            (jid,
             redact(summary if summary is not None else cur["summary"]),
             json.dumps(open_intents if open_intents is not None
                        else cur["open_intents"]),
             time.time()))
        c.commit()
    finally:
        c.close()


def add_intent(jid: str, intent: str, db_path: Path | None = None) -> None:
    """Record something unresolved. This is the anti-contradiction primitive."""
    intent = (intent or "").strip()
    if not intent:
        return
    cur = thread(jid, db_path=db_path)["open_intents"]
    if intent not in cur:
        update_thread(jid, open_intents=cur + [intent], db_path=db_path)


def resolve_intent(jid: str, needle: str, db_path: Path | None = None) -> bool:
    n = (needle or "").strip().lower()
    cur = thread(jid, db_path=db_path)["open_intents"]
    keep = [i for i in cur if n not in i.lower()]
    if len(keep) == len(cur):
        return False
    update_thread(jid, open_intents=keep, db_path=db_path)
    return True


def load(jid: str, db_path: Path | None = None) -> dict:
    """Everything a turn needs about one person. Called BEFORE every turn."""
    t = thread(jid, db_path=db_path)
    return {"jid": jid, "summary": t["summary"],
            "open_intents": t["open_intents"],
            "messages": history(jid, db_path=db_path)}


def as_context(jid: str, db_path: Path | None = None) -> str:
    """The state block that goes into the system prompt."""
    s = load(jid, db_path=db_path)
    if not (s["messages"] or s["summary"] or s["open_intents"]):
        return ""
    out = [f"\nWHAT YOU KNOW ABOUT {jid}"]
    if s["summary"]:
        out.append(f"Summary: {s['summary']}")
    if s["open_intents"]:
        out.append("UNRESOLVED - do not contradict these, they were observed:")
        out += [f"  - {i}" for i in s["open_intents"]]
    if s["messages"]:
        out.append("Recent messages:")
        for m in s["messages"]:
            who = "them" if m["direction"] == "in" else "you"
            out.append(f"  {who}: {m['text'][:200]}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Jobs — the only way anything waits
# ─────────────────────────────────────────────────────────────────────────────

def add_job(kind: str, params: dict, every_seconds: int | None = None,
            until: str | None = None, db_path: Path | None = None) -> int:
    now = time.time()
    c = _conn(db_path)
    try:
        cur = c.execute(
            "INSERT INTO jobs (kind, params, every_seconds, until, next_run_at,"
            " status, created_at) VALUES (?,?,?,?,?,'pending',?)",
            (kind, json.dumps(params or {}), every_seconds, until,
             now + (every_seconds or 0), now))
        c.commit()
        return int(cur.lastrowid)
    finally:
        c.close()


def due_jobs(now: float | None = None, db_path: Path | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    c = _conn(db_path)
    try:
        rows = c.execute(
            "SELECT * FROM jobs WHERE status='pending' AND next_run_at<=?"
            " ORDER BY next_run_at", (now,)).fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["params"] = json.loads(d["params"] or "{}")
        except Exception:
            d["params"] = {}
        out.append(d)
    return out


def reschedule(job_id: int, db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT every_seconds FROM jobs WHERE id=?",
                      (job_id,)).fetchone()
        gap = (r["every_seconds"] if r and r["every_seconds"] else 0) or 60
        c.execute("UPDATE jobs SET next_run_at=? WHERE id=?",
                  (time.time() + gap, job_id))
        c.commit()
    finally:
        c.close()


def finish_job(job_id: int, result: str = "", status: str = "done",
               db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        c.execute("UPDATE jobs SET status=?, result=? WHERE id=?",
                  (status, (result or "")[:2000], job_id))
        c.commit()
    finally:
        c.close()


def job(job_id: int, db_path: Path | None = None) -> dict | None:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    finally:
        c.close()
    return dict(r) if r else None


def delete_job(job_id: int, db_path: Path | None = None) -> bool:
    """Actually REMOVE a job. The owner said "delete" and meant delete - there
    was only finish_job (a status flip), so a job he was done with never left
    the queue or the LIVE WATCH panel. Returns whether a row was removed."""
    c = _conn(db_path)
    try:
        cur = c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def delete_all_jobs(db_path: Path | None = None) -> int:
    """Remove every job. Returns how many were deleted. Jobs are the worker's
    queue, not history worth keeping - 'remove all jobs' clears it."""
    c = _conn(db_path)
    try:
        cur = c.execute("DELETE FROM jobs")
        c.commit()
        return cur.rowcount
    finally:
        c.close()


def jobs(status: str = "", db_path: Path | None = None) -> list[dict]:
    c = _conn(db_path)
    try:
        if status:
            rows = c.execute("SELECT * FROM jobs WHERE status=? ORDER BY id DESC",
                             (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 50").fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Commitments and facts
# ─────────────────────────────────────────────────────────────────────────────

def add_commitment(what: str, who: str = "", due: str = "", source: str = "",
                   db_path: Path | None = None) -> int:
    c = _conn(db_path)
    try:
        cur = c.execute(
            "INSERT INTO commitments (who, what, due, source, status, created)"
            " VALUES (?,?,?,?,'open',?)", (who, what, due, source, time.time()))
        c.commit()
        return int(cur.lastrowid)
    finally:
        c.close()


def open_commitments(who: str = "", db_path: Path | None = None) -> list[dict]:
    c = _conn(db_path)
    try:
        if who:
            rows = c.execute("SELECT * FROM commitments WHERE status='open'"
                             " AND who LIKE ? ORDER BY id", (f"%{who}%",)).fetchall()
        else:
            rows = c.execute("SELECT * FROM commitments WHERE status='open'"
                             " ORDER BY id").fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def close_commitment(needle: str, db_path: Path | None = None) -> bool:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT id FROM commitments WHERE status='open'"
                      " AND what LIKE ? ORDER BY id DESC LIMIT 1",
                      (f"%{needle}%",)).fetchone()
        if not r:
            return False
        # Closed, never deleted - "did I already send that?" is a real question.
        c.execute("UPDATE commitments SET status='done' WHERE id=?", (r["id"],))
        c.commit()
        return True
    finally:
        c.close()


def set_fact(key: str, value: str, db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        c.execute("INSERT INTO facts (key, value, updated_at) VALUES (?,?,?)"
                  " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                  " updated_at=excluded.updated_at",
                  (key.strip().lower()[:60], redact(value), time.time()))
        c.commit()
    finally:
        c.close()


def facts(db_path: Path | None = None) -> dict:
    c = _conn(db_path)
    try:
        rows = c.execute("SELECT key, value FROM facts ORDER BY key").fetchall()
    finally:
        c.close()
    return {r["key"]: r["value"] for r in rows}


def facts_block(db_path: Path | None = None) -> str:
    """The changing part of the prompt: the clock first, then what is stored.

    THE DATE IS HERE BECAUSE ITS ABSENCE PRODUCED A CONFIDENT WRONG ANSWER.
    Asked what day it was, Jarvis said Monday. It was Saturday. Nothing in the
    prompt carried the date, so the model answered from its training prior
    rather than looking - and a model has no way to notice it does not know.

    Keys are printed too. "- 30 November 2026" alone is not a fact, it is a
    number; "project deadline: 30 November 2026" is one."""
    now = time.localtime()
    lines = [
        "",
        "RIGHT NOW",
        f"- {time.strftime('%A %d %B %Y, %H:%M', now)} ({time.strftime('%Z', now)})",
        "- Compute dates with code if they matter; never recall them.",
    ]
    f = facts(db_path=db_path)
    if f:
        lines += ["", "WHAT YOU ALREADY KNOW"]
        lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in f.items()]
    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# Spend
# ─────────────────────────────────────────────────────────────────────────────

def record_spend(model: str, input_tokens: int = 0, cached_tokens: int = 0,
                 output_tokens: int = 0, usd: float = 0.0,
                 db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        c.execute("INSERT INTO spend (ts, day, model, input_tokens,"
                  " cached_tokens, output_tokens, usd) VALUES (?,?,?,?,?,?,?)",
                  (time.time(), time.strftime("%Y-%m-%d"), model,
                   input_tokens, cached_tokens, output_tokens, usd))
        c.commit()
    finally:
        c.close()


def spend_today(db_path: Path | None = None) -> float:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT COALESCE(SUM(usd),0) t FROM spend WHERE day=?",
                      (time.strftime("%Y-%m-%d"),)).fetchone()
    finally:
        c.close()
    return float(r["t"] or 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Outreach — every time Jarvis disturbs the Boss without being asked
#
# One table answers three separate questions that would otherwise each grow
# their own counter: how long since the last interruption (the cooldown), how
# many calls today (the daily cap), and which job rang the phone (the audit
# trail). A monitor that becomes spam is a monitor that gets muted, and then
# the one alert that mattered arrives to a muted channel.
# ─────────────────────────────────────────────────────────────────────────────

def log_outreach(kind: str, reason: str = "", job_id: int | None = None,
                 critical: bool = False, db_path: Path | None = None) -> int:
    c = _conn(db_path)
    try:
        cur = c.execute(
            "INSERT INTO outreach (ts, day, kind, job_id, reason, critical)"
            " VALUES (?,?,?,?,?,?)",
            (time.time(), time.strftime("%Y-%m-%d"), kind, job_id,
             redact(reason)[:400], 1 if critical else 0))
        c.commit()
        return int(cur.lastrowid or 0)
    finally:
        c.close()


def last_outreach_at(db_path: Path | None = None) -> float:
    """When the Boss was last interrupted. 0.0 if never."""
    c = _conn(db_path)
    try:
        r = c.execute("SELECT COALESCE(MAX(ts),0) t FROM outreach").fetchone()
    finally:
        c.close()
    return float(r["t"] or 0.0)


def calls_today(db_path: Path | None = None) -> int:
    c = _conn(db_path)
    try:
        r = c.execute("SELECT COUNT(*) n FROM outreach WHERE day=? AND kind='call'",
                      (time.strftime("%Y-%m-%d"),)).fetchone()
    finally:
        c.close()
    return int(r["n"] or 0)


def outreach_log(limit: int = 20, db_path: Path | None = None) -> list[dict]:
    """Recent interruptions, newest first. What rang, why, and which job."""
    c = _conn(db_path)
    try:
        rows = c.execute("SELECT * FROM outreach ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def threads_moved_since(ts: float, db_path: Path | None = None) -> list[dict]:
    """Conversations that changed since a timestamp. The morning brief's input."""
    c = _conn(db_path)
    try:
        rows = c.execute(
            "SELECT t.jid, t.summary, t.updated_at, c.name"
            " FROM threads t LEFT JOIN contacts c ON c.jid = t.jid"
            " WHERE t.updated_at > ? AND COALESCE(t.summary,'') <> ''"
            " ORDER BY t.updated_at DESC", (ts,)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Errors that would otherwise vanish
#
# There were 34 `except Exception: pass` sites in this codebase. Most were
# defensible in isolation - a HUD that cannot paint one frame should not take
# the process down - but together they meant a failure could happen, be
# swallowed, and leave nothing anywhere for anyone to find. The bug then shows
# up hours later as behaviour nobody can explain.
#
# So swallowing is still allowed where continuing is genuinely right; what is
# no longer allowed is swallowing SILENTLY. This is the place it goes instead.
# ─────────────────────────────────────────────────────────────────────────────

ERROR_SCHEMA = """
CREATE TABLE IF NOT EXISTS errors (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    where_ TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT,
    fatal  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_errors_ts ON errors(ts);
"""
_ERRORS_READY = False
KEEP_ERRORS = 500


def record_error(where: str, exc: BaseException | str, fatal: bool = False,
                 db_path: Path | None = None) -> None:
    """Note that something went wrong. Never raises - this is the last resort."""
    global _ERRORS_READY
    try:
        kind = type(exc).__name__ if isinstance(exc, BaseException) else "note"
        detail = redact(str(exc))[:1000]
        c = _conn(db_path)
        try:
            if not _ERRORS_READY:
                c.executescript(ERROR_SCHEMA)
                _ERRORS_READY = True
            c.execute("INSERT INTO errors (ts, where_, kind, detail, fatal)"
                      " VALUES (?,?,?,?,?)",
                      (time.time(), str(where)[:120], kind, detail,
                       1 if fatal else 0))
            c.execute("DELETE FROM errors WHERE id NOT IN"
                      " (SELECT id FROM errors ORDER BY id DESC LIMIT ?)",
                      (KEEP_ERRORS,))
            c.commit()
        finally:
            c.close()
    except Exception:
        pass          # the error recorder itself must never break a caller


def recent_errors(limit: int = 50, db_path: Path | None = None) -> list[dict]:
    global _ERRORS_READY
    c = _conn(db_path)
    try:
        if not _ERRORS_READY:
            c.executescript(ERROR_SCHEMA)
            _ERRORS_READY = True
        rows = c.execute("SELECT * FROM errors ORDER BY id DESC LIMIT ?",
                         (limit,)).fetchall()
    finally:
        c.close()
    return [dict(r) for r in rows]


def clear_errors(db_path: Path | None = None) -> None:
    c = _conn(db_path)
    try:
        c.executescript(ERROR_SCHEMA)
        c.execute("DELETE FROM errors")
        c.commit()
    finally:
        c.close()
