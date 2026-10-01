"""
verify.py — check that the thing actually happened, without asking a model.

THE RULE THIS ENFORCES

    "Downloaded" means a file of the right kind is on disk.
    "Opened"     means a process exists.
    "Navigated"  means the page's URL is the one that was wanted.
    "Created"    means the path is there with the content that was asked for.
    "Succeeded"  means the exit code was zero.

    None of those need a language model, and using one for them is both slower
    and worse: a model asked "did the download work?" can only reason about
    what it was told, while os.path.exists() looks.

WHY IT IS A MODULE AND NOT A HABIT

    Because a habit gets skipped under pressure. Every verifier here returns
    the same shape - ok, plus the EVIDENCE that decided it - so a caller that
    wants to claim an outcome has one obvious way to earn the claim, and a
    reviewer can see what was actually measured rather than what was intended.

    `Evidence.why` is never empty on a failure. "It did not work" is not a
    finding; "expected a .pdf of at least 1 KB, found a 412-byte .html" is.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Evidence:
    """What was checked, what was found, and whether that settles it."""
    ok: bool
    what: str
    why: str = ""
    found: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.ok

    def as_dict(self) -> dict:
        return {"ok": self.ok, "what": self.what, "why": self.why,
                "found": self.found}


# ─────────────────────────────────────────────────────────────────────────────
# Filesystem
# ─────────────────────────────────────────────────────────────────────────────

def file_exists(path: str | Path, min_bytes: int = 1,
                suffix: str = "") -> Evidence:
    """A file is there, big enough to be real, and of the expected kind.

    min_bytes defaults to 1 because a zero-byte file is the classic shape of a
    failed download that still created its target - present, and useless."""
    p = Path(path)
    if not p.exists():
        return Evidence(False, f"file {p.name}", f"nothing exists at {p}")
    if not p.is_file():
        return Evidence(False, f"file {p.name}", f"{p} is a directory")
    size = p.stat().st_size
    if size < min_bytes:
        return Evidence(False, f"file {p.name}",
                        f"only {size} bytes; expected at least {min_bytes}",
                        {"bytes": size})
    if suffix and p.suffix.lower() != suffix.lower():
        return Evidence(False, f"file {p.name}",
                        f"is a {p.suffix or 'no-extension'} file, expected "
                        f"{suffix}", {"suffix": p.suffix, "bytes": size})
    return Evidence(True, f"file {p.name}", "",
                    {"path": str(p), "bytes": size, "suffix": p.suffix})


def file_contains(path: str | Path, text: str) -> Evidence:
    p = Path(path)
    if not p.is_file():
        return Evidence(False, f"content of {p.name}", f"no file at {p}")
    try:
        body = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return Evidence(False, f"content of {p.name}", f"unreadable: {e}")
    if text.lower() not in body.lower():
        return Evidence(False, f"content of {p.name}",
                        f"does not contain {text!r} (read {len(body)} chars)",
                        {"chars": len(body)})
    return Evidence(True, f"content of {p.name}", "",
                    {"chars": len(body), "matched": text[:60]})


def dir_has(path: str | Path, count: int = 1, pattern: str = "*") -> Evidence:
    p = Path(path)
    if not p.is_dir():
        return Evidence(False, f"directory {p.name}", f"no directory at {p}")
    hits = [x for x in p.glob(pattern) if x.is_file()]
    if len(hits) < count:
        return Evidence(False, f"directory {p.name}",
                        f"holds {len(hits)} files matching {pattern!r}, "
                        f"expected at least {count}", {"found": len(hits)})
    return Evidence(True, f"directory {p.name}", "",
                    {"found": len(hits), "names": [x.name for x in hits[:10]]})


# ─────────────────────────────────────────────────────────────────────────────
# Processes
# ─────────────────────────────────────────────────────────────────────────────

def process_running(name_or_pid, timeout: float = 0.0) -> Evidence:
    """A process exists. Waits only if a timeout is given, and polls rather
    than sleeping a fixed amount, so a fast launch is not paid for."""
    import psutil

    def look():
        if isinstance(name_or_pid, int) or str(name_or_pid).isdigit():
            pid = int(name_or_pid)
            return [pid] if psutil.pid_exists(pid) else []
        frag = str(name_or_pid).lower()
        return [p.pid for p in psutil.process_iter(["pid", "name"])
                if frag in (p.info.get("name") or "").lower()]

    deadline = time.time() + max(0.0, timeout)
    pids = look()
    while not pids and time.time() < deadline:
        time.sleep(0.15)
        pids = look()

    if not pids:
        return Evidence(False, f"process {name_or_pid}",
                        f"no process matching {name_or_pid!r} is running"
                        + (f" after waiting {timeout:.0f}s" if timeout else ""))
    return Evidence(True, f"process {name_or_pid}", "",
                    {"pids": pids[:10], "count": len(pids)})


def process_gone(name_or_pid, timeout: float = 5.0) -> Evidence:
    """The opposite, and NOT just `not process_running` - this waits for the
    process to actually die, because kill() returns before the OS has reaped."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_running(name_or_pid):
            return Evidence(True, f"process {name_or_pid} gone")
        time.sleep(0.15)
    ev = process_running(name_or_pid)
    return Evidence(False, f"process {name_or_pid} gone",
                    f"still running after {timeout:.0f}s", ev.found)


# ─────────────────────────────────────────────────────────────────────────────
# Commands and pages
# ─────────────────────────────────────────────────────────────────────────────

def command_succeeded(result: dict) -> Evidence:
    """A shell result really means success - exit zero and not timed out."""
    if not isinstance(result, dict):
        return Evidence(False, "command", f"not a result dict: {type(result)}")
    if result.get("timed_out"):
        return Evidence(False, "command",
                        f"timed out: {result.get('command', '')[:80]}")
    code = result.get("exit_code")
    if code != 0:
        why = (result.get("error") or result.get("stderr") or "")[:200]
        return Evidence(False, "command",
                        f"exited {code}: {why}", {"exit_code": code})
    return Evidence(True, "command", "",
                    {"exit_code": 0, "seconds": result.get("seconds")})


def page_is(expected_fragment: str, actual_url: str = "") -> Evidence:
    """The browser is on the page that was wanted, by URL rather than by hope."""
    url = actual_url
    if not url:
        try:
            import browser
            url = browser.on_page(lambda p: p.url) or ""
        except Exception as e:
            return Evidence(False, "page", f"could not read the URL: {e}")
    if expected_fragment.lower() not in (url or "").lower():
        return Evidence(False, "page",
                        f"on {url or '(nothing)'}, expected something "
                        f"containing {expected_fragment!r}", {"url": url})
    return Evidence(True, "page", "", {"url": url})


# ─────────────────────────────────────────────────────────────────────────────
# Putting several together
# ─────────────────────────────────────────────────────────────────────────────

def all_of(*checks: Evidence) -> Evidence:
    """Every check must hold. Reports the FIRST failure with its reason, so a
    caller gets something actionable rather than a count."""
    failed = [c for c in checks if not c.ok]
    if failed:
        return Evidence(False, "all of " + ", ".join(c.what for c in checks),
                        failed[0].why,
                        {"failed": [c.what for c in failed],
                         "reasons": [c.why for c in failed][:5]})
    return Evidence(True, "all of " + ", ".join(c.what for c in checks), "",
                    {"checked": len(checks)})


def outcome(task_id: int, *checks: Evidence, db_path=None) -> Evidence:
    """Verify a task's real outcome and record the evidence on the task.

    A task moves to COMPLETED only through here, so "completed" always has
    something measured behind it."""
    import tasks
    ev = all_of(*checks) if checks else Evidence(False, "nothing",
                                                 "no checks were supplied")
    t = tasks.get(task_id, db_path)
    if t is None:
        return ev
    if ev.ok:
        if t["state"] is not tasks.State.VERIFYING:
            try:
                tasks.move(task_id, tasks.State.VERIFYING,
                           note="checking the real outcome", db_path=db_path)
            except tasks.IllegalTransition:
                pass
        tasks.move(task_id, tasks.State.COMPLETED, note=ev.what,
                   result=str(ev.found)[:2000], db_path=db_path)
    else:
        tasks.move(task_id, tasks.State.RECOVERING,
                   note="the outcome did not check out", error=ev.why,
                   db_path=db_path)
    return ev
