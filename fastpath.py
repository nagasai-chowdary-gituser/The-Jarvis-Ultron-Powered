"""
fastpath.py — answer the obvious things without asking a model.

"Open Chrome" took five seconds. Not because opening Chrome is slow - it takes
about eighty milliseconds - but because the request went to a reasoning model,
which planned, emitted a tool call, waited for the result, and then composed a
sentence about it. Ninety-eight percent of that was the model.

Nothing in "open chrome" is ambiguous. It does not need planning, it needs
doing. So requests that are unmistakable are matched here, executed directly,
and answered from a template. No model, no tokens, no round trip.

THE BAR FOR ADDING A PATTERN IS DELIBERATELY HIGH

    A fast path that fires on something it misreads is worse than no fast path,
    because it does the wrong thing CONFIDENTLY and quickly. So every pattern
    here must be:

      - unambiguous          "open chrome" can only mean one thing
      - read-only or trivially reversible   opening an app, reading the clock
      - self-verifying       the tool returns proof, which goes in the answer

    Anything that sends, deletes, spends, or could mean two things falls
    through to the model. When in doubt, fall through: the cost of falling
    through is a few seconds, and the cost of a wrong guess is trust.

THIS IS §9 OF THE SPEC, AND IT IS THE SINGLE LARGEST LATENCY WIN AVAILABLE:
do not use a language model where deterministic code is faster.
"""
from __future__ import annotations

import re
import time

# ─────────────────────────────────────────────────────────────────────────────
# Patterns. Each returns (handler, kwargs) or None.
# ─────────────────────────────────────────────────────────────────────────────

# A SPECIFIC Chrome profile - checked before _OPEN, or "open my 5th profile"
# would be read as "open an app called 'my 5th profile'". Captures the profile
# reference ("main", "5th", "rudrxai") before the word "profile".
_CHROME_PROFILE = re.compile(
    r"^(?:please\s+)?(?:open|launch|switch\s+to|go\s+to)\s+(?:my\s+|the\s+)?"
    r"(?P<which>.+?)\s+(?:chrome\s+|google\s+)?profile"
    r"(?:\s+(?:in\s+)?(?:chrome(?:\s+app)?|the\s+chrome\s+app))?\s*$", re.I)

_OPEN = re.compile(
    r"^(?:please\s+)?(?:can\s+you\s+)?(?:just\s+)?"
    r"(?:open|launch|start|run)\s+"
    r"(?:the\s+|my\s+)?(?P<app>[a-z0-9 .+&'-]{2,40}?)"
    r"(?:\s+(?:app|application|for\s+me|please|now))?$", re.I)

_MEDIA = re.compile(
    r"^(?:please\s+)?(?P<action>play|pause|next|previous|skip|"
    r"volume\s*up|volume\s*down|mute|unmute)"
    r"(?:\s+(?:the\s+)?(?:music|song|track|video|media))?$", re.I)

# "set up my browser login" - open Jarvis's Chrome for a one-time sign-in.
_LOGIN_SETUP = re.compile(
    r"^(?:set\s*up|setup|configure|fix)\s+(?:my\s+|the\s+)?"
    r"(?:browser|chrome|jarvis(?:'s)?)?\s*(?:browser\s+)?login(?:s)?$", re.I)

# Google: connect once, then read calendar / inbox.
_GOOGLE_CONNECT = re.compile(
    r"^(?:connect|set\s*up|setup|link|sign\s*in\s*to)\s+(?:my\s+|the\s+)?google"
    r"(?:\s+account)?$", re.I)
_CALENDAR = re.compile(
    r"^(?:what(?:'s|s| is)?\s+)?(?:on\s+)?(?:my\s+|the\s+)?"
    r"(?:calendar|schedule|agenda|meetings?|events?)"
    r"(?:\s+(?:today|now|coming up|next|this week))?\s*\??$", re.I)
_EMAIL = re.compile(
    r"^(?:check\s+)?(?:any\s+)?(?:my\s+|new\s+|unread\s+)*"
    r"(?:e-?mails?|inbox|mails?)(?:\s+(?:now|today|please))?\s*\??$", re.I)

# Job management - "delete all jobs", "stop and remove all jobs", "delete job 10".
_JOBS_DELETE_ALL = re.compile(
    r"^(?:stop\s+and\s+)?(?:delete|remove|clear|cancel|stop|kill)\s+"
    r"(?:all\s+)?(?:the\s+|my\s+)?jobs?$", re.I)
_JOB_DELETE_ONE = re.compile(
    r"^(?:delete|remove|cancel|stop|kill)\s+(?:the\s+)?job\s+#?(?P<n>\d+)$", re.I)

_TIME = re.compile(
    r"^(?:what(?:'s|s| is)?\s+)?(?:the\s+)?"
    r"(?:time|date|day|today)"
    r"(?:\s+(?:is\s+it|it\s+is|now|today|is\s+today))?\s*\??$", re.I)

_STATS = re.compile(
    r"^(?:what(?:'s| is)\s+)?(?:my\s+|the\s+)?"
    r"(?:cpu|ram|memory|disk|storage|system)\s*"
    r"(?:usage|stats|status|free|space|load)?\??$", re.I)

_APPS = re.compile(r"^(?:what|which)\s+apps?\s+(?:are\s+)?"
                   r"(?:installed|available|do i have)\??$", re.I)

_RUNNING = re.compile(r"^(?:what(?:'s|s| is)?\s+)?(?:is\s+)?running"
                      r"(?:\s+(?:now|right now|on my (?:pc|laptop|computer)))?"
                      r"\s*\??$", re.I)

# Words that mean this is NOT a trivial request even if it looks like one.
# NOTE: "schedule" is deliberately NOT here - it blocked "my schedule today"
# (the calendar) from ever reaching the calendar route. "schedule a reminder"
# still falls through to the model, because no fast path matches it.
_COMPLEX = re.compile(
    r"\b(and then|then|after that|also|if |when |until |every |"
    r"remind|monitor|watch|compare|research|summar|explain|why|fix|debug)\b",
    re.I)

# Apps a bare name should never resolve to something destructive.
_APP_STOPWORDS = {"it", "that", "this", "up", "down", "the", "a", "an",
                  "file", "files", "folder", "them", "everything"}


def _do_open(app: str) -> dict:
    from lib import desktop
    r = desktop.open_app(app)
    return {"ok": True, "say": f"{r['launched']} is open.", "evidence": r}


def _do_chrome_profile(which: str) -> dict:
    from lib import desktop
    r = desktop.open_chrome_profile(which)
    if not r.get("ok"):
        # Ambiguous or unreadable -> let the model handle it (it can list them).
        raise ValueError(r.get("error", "no matching profile"))
    name = r.get("opened") or which
    email = r.get("email")
    say = f"Opened your {name} profile in Chrome" + (f" ({email})." if email else ".")
    return {"ok": True, "say": say, "evidence": r}


def _do_setup_login(_: str = "") -> dict:
    import browser
    r = browser.open_for_login()
    if not r.get("ok"):
        raise ValueError(r.get("error", "could not open the browser"))
    return {"ok": True,
            "say": "Opened Jarvis's own Chrome at the OpenAI sign-in page. Sign "
                   "in once in that window - it stays signed in for every future "
                   "task.",
            "evidence": r}


def _do_google_connect(_: str = "") -> dict:
    from lib import google
    r = google.setup()
    if not r.get("ok"):
        raise ValueError(r.get("error", "could not start Google sign-in"))
    return {"ok": True, "say": r.get("note", "Google is connected."),
            "evidence": r}


def _do_calendar(_: str = "") -> dict:
    from lib import google
    r = google.calendar_upcoming(hours=24)
    if not r.get("ok"):
        raise ValueError(r.get("error", "calendar unavailable"))
    evs = r.get("events") or []
    if not evs:
        return {"ok": True, "say": "Nothing on your calendar in the next day.",
                "evidence": r}
    parts = [f"{e['summary']} at {str(e['start'])[11:16] or e['start']}"
             for e in evs[:5]]
    return {"ok": True, "say": "Coming up: " + "; ".join(parts) + ".",
            "evidence": r}


def _do_email(_: str = "") -> dict:
    from lib import google
    r = google.gmail_unread(8)
    if not r.get("ok"):
        raise ValueError(r.get("error", "email unavailable"))
    un = r.get("unread") or []
    if not un:
        return {"ok": True, "say": "No unread inbox email.", "evidence": r}
    first = un[0]
    frm = first["from"].split("<")[0].strip().strip('"')
    say = (f"{len(un)} unread. Latest from {frm[:30]}: "
           f"{first['subject'][:60]}.")
    return {"ok": True, "say": say, "evidence": r}


def _do_delete_all_jobs(_: str = "") -> dict:
    import state
    n = state.delete_all_jobs()
    say = (f"Removed {n} job{'s' if n != 1 else ''}." if n
           else "There were no jobs to remove.")
    return {"ok": True, "say": say, "evidence": {"deleted": n}}


def _do_delete_job(n: str) -> dict:
    import state
    ok = state.delete_job(int(n))
    say = f"Deleted job {n}." if ok else f"There is no job {n}."
    return {"ok": True, "say": say, "evidence": {"id": int(n), "deleted": ok}}


def _do_media(action: str) -> dict:
    from lib import desktop
    a = action.lower().replace(" ", "")
    a = {"skip": "next", "unmute": "mute"}.get(a, a)
    r = desktop.media(a)
    return {"ok": True, "say": f"Done - {a}.", "evidence": r}


def _do_time(_: str = "") -> dict:
    now = time.localtime()
    return {"ok": True,
            "say": time.strftime("It is %A %d %B %Y, %I:%M %p.", now).replace(" 0", " "),
            "evidence": {"iso": time.strftime("%Y-%m-%dT%H:%M:%S", now)}}


def _do_stats(_: str = "") -> dict:
    from lib import desktop
    s = desktop.stats()
    return {"ok": True,
            "say": (f"CPU {s['cpu_percent']:.0f} percent, memory "
                    f"{s['memory_percent']:.0f} percent, "
                    f"{s['memory_free_gb']:.1f} gigabytes free, "
                    f"{s['disk_free_gb']:.0f} gigabytes free on C. "
                    f"{s['os']}, {s['cpu_count']} cores."),
            "evidence": s}


def _do_apps(_: str = "") -> dict:
    from lib import desktop
    apps = desktop.installed_apps()
    names = ", ".join(a["name"] for a in apps[:12])
    return {"ok": True,
            "say": f"{len(apps)} apps installed. The first few: {names}.",
            "evidence": {"count": len(apps)}}


def _do_running(_: str = "") -> dict:
    from lib import desktop
    procs = desktop.processes()
    seen, names = set(), []
    for p in procs:
        n = p["name"].replace(".exe", "")
        if n.lower() not in seen:
            seen.add(n.lower())
            names.append(n)
    return {"ok": True,
            "say": f"{len(seen)} programs running, including "
                   f"{', '.join(names[:8])}.",
            "evidence": {"count": len(seen)}}


_ROUTES = (
    (_TIME, _do_time, None),
    (_STATS, _do_stats, None),
    (_APPS, _do_apps, None),
    (_RUNNING, _do_running, None),
    (_MEDIA, _do_media, "action"),
    (_LOGIN_SETUP, _do_setup_login, None),
    (_GOOGLE_CONNECT, _do_google_connect, None),
    (_CALENDAR, _do_calendar, None),
    (_EMAIL, _do_email, None),
    (_JOBS_DELETE_ALL, _do_delete_all_jobs, None),
    (_JOB_DELETE_ONE, _do_delete_job, "n"),
    (_CHROME_PROFILE, _do_chrome_profile, "which"),   # BEFORE _OPEN
    (_OPEN, _do_open, "app"),
)


def match(text: str):
    """Return a zero-argument callable, or None to let the model handle it."""
    t = (text or "").strip().rstrip(".!")
    if not t or len(t) > 80:
        return None
    if _COMPLEX.search(t):
        return None                       # more than one thing is being asked

    for pattern, handler, group in _ROUTES:
        m = pattern.match(t)
        if not m:
            continue
        if group is None:
            return lambda h=handler: h("")
        value = (m.group(group) or "").strip()
        if not value or value.lower() in _APP_STOPWORDS:
            return None
        return lambda h=handler, v=value: h(v)
    return None


def try_run(text: str) -> dict | None:
    """Run the fast path if it applies. Returns None to fall through.

    A handler that RAISES also falls through rather than failing the turn -
    "open whatsapp" with an ambiguous name should reach the model, which can
    ask which one, not die here with a ValueError."""
    fn = match(text)
    if fn is None:
        return None
    t0 = time.perf_counter()
    try:
        out = fn()
    except Exception:
        return None
    out["seconds"] = time.perf_counter() - t0
    out["via"] = "fastpath"
    return out
