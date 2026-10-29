"""
memory.py — intelligent, semantic memory. The part that makes Jarvis learn.

WHAT WAS WRONG WITH THE OLD MEMORY

    state.facts was a flat key -> value table and state.history was the last
    ~12 lines of the current channel. So Jarvis "remembered" only what it had
    been told to store under an exact key, and only what was said minutes ago.
    Ask it something it learned last week in different words and it had nothing:
    the key did not match and the line had scrolled off. That is not memory,
    it is a scratchpad.

WHAT THIS IS

    A store of short memories, each with an embedding, searched by MEANING. "my
    college is Saveetha" and "what school do I go to" land near each other even
    though they share no words. Every turn:

      - the request is embedded once and the most RELEVANT memories (not the
        most recent) are pulled into the prompt, so the brain answers from what
        it actually knows;
      - after the turn, what was learned is captured in the background - the
        exchange as an episodic memory, plus any durable facts a quick model
        pass pulls out - so next time it is already known.

    Brute-force cosine over a cached matrix. For one person's memory (hundreds
    to a few thousand rows) that is microseconds and needs no vector database.

NEVER BREAKS A TURN. Embeddings down? recall() falls back to keyword search and
capture() silently skips. Memory is an advantage, not a dependency.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

import numpy as np

import models
import state

EMBED_MODEL = getattr(models, "EMBED", "text-embedding-3-small")
EMBED_DIM = 1536
# text-embedding-3-small runs low in absolute cosine - 0.3 is "clearly related" -
# so thresholds are low on purpose. Better to surface a borderline memory and let
# the orchestrator judge it than to hide something useful.
TOP_K = 6
MIN_SIM = 0.18            # below this, a "match" is just noise
DEDUP_SIM = 0.86         # an obvious paraphrase of a stored fact -> merge, not pile up

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    embedding  BLOB,
    kind       TEXT NOT NULL DEFAULT 'fact',   -- fact | preference | episodic | contact
    source     TEXT,                            -- desk | telegram | phone | capture
    importance REAL NOT NULL DEFAULT 1.0,
    uses       INTEGER NOT NULL DEFAULT 0,
    ts         REAL NOT NULL,
    last_used  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_mem_kind ON memories(kind);
"""

_ready = False
_lock = threading.Lock()
# In-RAM cache of (ids, matrix, texts) so search does not hit disk or re-embed.
_cache: dict | None = None
_cache_count = -1


def _conn() -> sqlite3.Connection:
    global _ready
    c = sqlite3.connect(state.DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    if not _ready:
        with _lock:
            c.executescript(_SCHEMA)
            _ready = True
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Embeddings
# ─────────────────────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> list[np.ndarray] | None:
    """Embed a batch. Returns None on any failure - callers degrade, never crash."""
    texts = [(t or "").strip()[:2000] for t in texts if (t or "").strip()]
    if not texts:
        return []
    try:
        from openai import OpenAI
        r = OpenAI(timeout=30, max_retries=1).embeddings.create(
            model=EMBED_MODEL, input=texts)
        return [np.asarray(d.embedding, dtype=np.float32) for d in r.data]
    except Exception as e:
        state.record_error("memory.embed", e)
        return None


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n else v


# ─────────────────────────────────────────────────────────────────────────────
# Writing
# ─────────────────────────────────────────────────────────────────────────────

def remember(text: str, kind: str = "fact", source: str = "",
             importance: float = 1.0, trusted: bool = True) -> int | None:
    """Store one memory. Near-duplicates are merged (importance bumped), not piled up.

    POISONING GUARD: nothing that looks like an injected command or a credential
    is ever stored (safety.unsafe_to_learn). And a PREFERENCE - a standing rule
    about how the Boss likes things, which then steers every turn - is accepted
    ONLY from a trusted source (his own messages), never from content Jarvis read
    off the web / a chat / an email. Untrusted FACTS are allowed (he asked to
    learn from the internet when it is safe), but untrusted behaviour rules are
    not, so a page can never rewrite how Jarvis acts."""
    import safety
    text = (text or "").strip()
    if len(text) < 3:
        return None
    if safety.unsafe_to_learn(text):
        return None                          # injected order or a secret: never store
    if kind == "preference" and not trusted:
        return None                          # behaviour rules only from the Boss
    text = state.redact(text)
    vecs = embed([text])
    vec = vecs[0] if vecs else None

    # Dedup against what is already known, by meaning. On a hit, the NEWER
    # wording replaces the stored one (latest wins) - so "keep replies short"
    # updated to "keep replies to one line" refreshes the rule instead of
    # leaving the stale phrasing in place. The sharper contradiction case
    # ("do X" -> "don't X") is handled by the capture pass's supersede list.
    if vec is not None:
        hit = _search_vec(vec, k=1)
        if hit and hit[0]["sim"] >= DEDUP_SIM:
            _bump(hit[0]["id"], importance, text=text[:2000])
            return hit[0]["id"]

    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO memories (text, embedding, kind, source, importance, ts)"
            " VALUES (?,?,?,?,?,?)",
            (text[:2000], vec.tobytes() if vec is not None else None,
             kind, source, importance, time.time()))
        c.commit()
        _invalidate()
        return int(cur.lastrowid)
    finally:
        c.close()


def _bump(mem_id: int, importance: float, text: str | None = None) -> None:
    """Reinforce an existing memory: raise its importance and touch it. If a
    newer wording is given, adopt it (the embedding stays - it is a paraphrase
    by construction, since this only runs on a dedup hit) and refresh the cache
    so the new text is what gets recalled and shown."""
    c = _conn()
    try:
        if text:
            c.execute("UPDATE memories SET importance = MIN(5.0, importance + ?),"
                      " last_used = ?, text = ? WHERE id = ?",
                      (max(0.1, importance * 0.5), time.time(), text, mem_id))
        else:
            c.execute("UPDATE memories SET importance = MIN(5.0, importance + ?),"
                      " last_used = ? WHERE id = ?",
                      (max(0.1, importance * 0.5), time.time(), mem_id))
        c.commit()
    finally:
        c.close()
    if text:
        _invalidate()


def forget(mem_id: int) -> None:
    c = _conn()
    try:
        c.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        c.commit()
        _invalidate()
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# Reading
# ─────────────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    global _cache, _cache_count
    c = _conn()
    try:
        rows = c.execute(
            "SELECT id, text, embedding, importance FROM memories").fetchall()
    finally:
        c.close()
    ids, texts, imps, vecs = [], [], [], []
    for r in rows:
        if r["embedding"] is None:
            continue
        v = np.frombuffer(r["embedding"], dtype=np.float32)
        if v.shape[0] != EMBED_DIM:
            continue
        ids.append(r["id"]); texts.append(r["text"]); imps.append(r["importance"])
        vecs.append(_unit(v))
    mat = np.vstack(vecs) if vecs else np.zeros((0, EMBED_DIM), dtype=np.float32)
    _cache = {"ids": ids, "texts": texts, "imps": np.asarray(imps or [0.0]),
              "mat": mat}
    _cache_count = len(ids)
    return _cache


def _invalidate() -> None:
    global _cache
    _cache = None


def _search_vec(vec: np.ndarray, k: int = TOP_K) -> list[dict]:
    cache = _cache or _load_cache()
    if cache["mat"].shape[0] == 0:
        return []
    q = _unit(vec)
    sims = cache["mat"] @ q                      # cosine (both unit)
    # Nudge by importance so a strong fact outranks a weak near-tie.
    score = sims + 0.03 * np.clip(cache["imps"][:len(sims)], 0, 5)
    order = np.argsort(-score)[:k]
    out = []
    for i in order:
        out.append({"id": cache["ids"][i], "text": cache["texts"][i],
                    "sim": float(sims[i])})
    return out


def recall(query: str, k: int = TOP_K) -> list[dict]:
    """The most RELEVANT memories for this query, by meaning. Keyword fallback."""
    query = (query or "").strip()
    if not query:
        return []
    vecs = embed([query])
    if vecs:
        return [h for h in _search_vec(vecs[0], k) if h["sim"] >= MIN_SIM]
    # Embeddings unavailable: substring fallback so recall still does something.
    q = query.lower()
    c = _conn()
    try:
        rows = c.execute("SELECT id, text FROM memories WHERE lower(text) LIKE ?"
                         " ORDER BY importance DESC LIMIT ?",
                         (f"%{q[:40]}%", k)).fetchall()
    finally:
        c.close()
    return [{"id": r["id"], "text": r["text"], "sim": 0.0} for r in rows]


def recall_block(query: str, k: int = TOP_K) -> str:
    """A prompt-ready block of what Jarvis knows that bears on this request."""
    hits = recall(query, k)
    if not hits:
        return ""
    # Mark them used, so genuinely useful memories rise over time.
    ids = [h["id"] for h in hits]
    try:
        c = _conn()
        c.execute(f"UPDATE memories SET uses = uses + 1, last_used = ? "
                  f"WHERE id IN ({','.join('?' * len(ids))})",
                  (time.time(), *ids))
        c.commit(); c.close()
    except Exception:
        pass
    lines = ["", "WHAT YOU REMEMBER THAT BEARS ON THIS (most relevant first):"]
    lines += [f"- {h['text']}" for h in hits]
    return "\n".join(lines) + "\n"


def count() -> int:
    c = _conn()
    try:
        return int(c.execute("SELECT COUNT(*) n FROM memories").fetchone()["n"])
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# Learning — capture what a turn taught, in the background
# ─────────────────────────────────────────────────────────────────────────────

_CAPTURE_PROMPT = (
    "From this exchange, extract two things, learning the way a good assistant "
    "learns his boss:\n"
    "1. FACTS - durable truths about the user or his world (his name, people, "
    "accounts, projects, decisions).\n"
    "2. PREFERENCES - HOW he likes things done, ESPECIALLY any correction he "
    "made to how Jarvis just did something. If he says 'no, keep it shorter', "
    "'always CC Priya', 'don't call me before noon', 'I prefer X', 'from now "
    "on Y', or corrects a mistake, capture the LESSON as a standing rule "
    "('Boss wants emails kept to 3 lines', 'Boss wants Priya CC'd on client "
    "mail'). ALSO learn HOW HE PHRASES things - his shorthand and what it really "
    "means - when he clarifies or corrects a misread: capture the mapping as a "
    "rule ('When Boss says \"make a pdf in chatgpt\" he means use ChatGPT to "
    "produce the content, then save it as a PDF', 'By \"my Nth account\" Boss "
    "means his Nth Chrome profile'), so the same request is read right next "
    "time.\n"
    "ONE short, self-contained sentence each. Skip small talk, one-off requests "
    "and anything already obvious.\n"
    "3. SUPERSEDES - if this exchange CHANGES or REVERSES one of the EXISTING "
    "standing rules shown below, copy that old rule's text VERBATIM into "
    "'supersedes' so it can be removed (a rule must never sit next to its own "
    "opposite). Only list a rule you are actually overriding.\n"
    "4. LESSONS - learn to do better from WHAT HAPPENED (the trace below, if "
    "any). If something FAILED and was then fixed, record the lesson so it is "
    "not repeated ('To open ChatGPT in his account, use open_chrome_profile with "
    "the url, not the automation browser'). If a multi-step task WORKED, record "
    "the proven flow as a reusable procedure ('To make a PDF from ChatGPT: open "
    "the profile at chatgpt.com, read the answer, then make_docx/pdf from it'). "
    "Each lesson: one actionable sentence, starting with the task it applies to.\n"
    "Output ONLY JSON: {\"facts\": [...], \"preferences\": [...], "
    '"supersedes": [...], "lessons": [...]}. Empty arrays if none.')


def capture_turn(user_text: str, reply: str, source: str = "",
                 trace: str = "") -> None:
    """Learn from one exchange, off the turn's critical path.

    A daemon thread: an episodic trace goes in at once, then a cheap model pass
    pulls out durable FACTS, PREFERENCES (how the Boss likes things done), and -
    from the TOOL TRACE of what actually happened - LESSONS (what worked / what
    failed and the fix), so Jarvis gets better at DOING over time, not just at
    knowing him. Facts/prefs come from HIS message (trusted); nothing that looks
    injected or secret is ever stored (the remember() guard). Failure is
    swallowed - learning must never cost a reply."""
    def work() -> None:
        try:
            ut = (user_text or "").strip()
            if len(ut) >= 6:
                remember(ut, kind="episodic", source=source, importance=0.5)
            if len(ut) < 12 and not trace:
                return                      # "hi"/"ok" teaches nothing durable
            from openai import OpenAI
            # Show the model the rules it already holds, so it can tell a NEW
            # rule from a CHANGE to an old one and name what to supersede.
            existing = preferences_block()
            content = f"USER: {ut[:1500]}\nJARVIS: {(reply or '')[:800]}\n"
            if trace:
                content += f"\nWHAT HAPPENED (tool trace):\n{trace[:1200]}\n"
            content += existing
            r = OpenAI(timeout=30, max_retries=1).responses.create(
                model=models.BRAIN,
                instructions=_CAPTURE_PROMPT,
                input=[{"role": "user", "content": content}],
                reasoning={"effort": "low"},
                max_output_tokens=600, store=False)
            models.record(r, models.BRAIN)
            raw = (r.output_text or "").strip()
            a, b = raw.find("{"), raw.rfind("}")
            obj = json.loads(raw[a:b + 1]) if (a >= 0 and b > a) else {}
            # Supersede FIRST: drop a rule the Boss has changed, so his new one
            # does not land next to its own opposite (both would be injected).
            for old in (obj.get("supersedes") or [])[:4]:
                if isinstance(old, str):
                    _forget_preference(old)
            for f in (obj.get("facts") or [])[:6]:
                if isinstance(f, str) and len(f.strip()) >= 6:
                    remember(f.strip(), kind="fact", source="capture",
                             importance=1.5)
            for p in (obj.get("preferences") or [])[:6]:
                if isinstance(p, str) and len(p.strip()) >= 6:
                    remember(p.strip(), kind="preference", source="capture",
                             importance=2.5)        # heavier: a standing rule
            # LESSONS - proven flows and failure-fixes, recalled for similar tasks.
            for le in (obj.get("lessons") or [])[:4]:
                if isinstance(le, str) and len(le.strip()) >= 10:
                    remember(le.strip(), kind="lesson", source="capture",
                             importance=2.0)
        except Exception as e:
            state.record_error("memory.capture", e)

    threading.Thread(target=work, daemon=True, name="memory-capture").start()


def preferences_block(limit: int = 14) -> str:
    """Everything Jarvis has learned about HOW the Boss likes things done.

    Unlike recall_block (semantic, per-request), preferences are few and always
    relevant, so they go into EVERY turn - that is what makes Jarvis follow his
    standing rules instead of needing to be corrected again."""
    c = _conn()
    try:
        rows = c.execute(
            "SELECT text FROM memories WHERE kind='preference' "
            "ORDER BY importance DESC, last_used DESC LIMIT ?", (limit,)).fetchall()
    finally:
        c.close()
    if not rows:
        return ""
    lines = ["", "HOW THE BOSS LIKES THINGS DONE (his standing rules - follow them):"]
    lines += [f"- {r['text']}" for r in rows]
    return "\n".join(lines) + "\n"


def _forget_preference(old_text: str) -> bool:
    """Remove a standing rule the Boss has changed. Matches the text the capture
    model was shown verbatim (exact first, then a loose contains), among
    PREFERENCES only - a superseded rule should never take a fact with it."""
    old = (old_text or "").strip().lower().lstrip("- ")
    if len(old) < 3:
        return False
    c = _conn()
    try:
        row = c.execute("SELECT id FROM memories WHERE kind='preference' "
                        "AND lower(text)=?", (old,)).fetchone()
        if row is None:
            row = c.execute("SELECT id FROM memories WHERE kind='preference' "
                            "AND lower(text) LIKE ? ORDER BY length(text) LIMIT 1",
                            (f"%{old[:60]}%",)).fetchone()
    finally:
        c.close()
    if row is not None:
        forget(int(row["id"]))
        return True
    return False
