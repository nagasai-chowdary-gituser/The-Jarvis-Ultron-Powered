"""
selfheal.py — notice what is broken and fix what is safe to fix.

Self-evaluation (state.success_summary) tells the Boss how often Jarvis succeeds.
Self-HEAL is the other half he asked for: when a failure is one Jarvis can repair
itself, it repairs it instead of handing him a problem. Two concrete, bounded
forms - not a vague promise:

1. ensure_module(name): the commonest recoverable failure in a code-writing agent
   is "No module named X" - it wrote code that needs a library the machine does
   not have. ensure_module installs it from PyPI (ONE attempt, a timeout, a
   validated name) and re-checks the import. run_python calls this automatically
   on a ModuleNotFoundError and retries the code once, so the agent acquires what
   it needs mid-task. This is why it can do "something nobody anticipated" (§2)
   even when the library for it is not pre-installed.

2. health_check(): a readable check across its own components - do the core
   modules import, is the database writable, are the models configured, is the
   key present, are recipes discoverable. Asked "are you okay?", Jarvis runs this
   and answers from what it found, not from a guess.

BOUNDED AND SAFE. ensure_module validates the name against a strict pattern (no
shell metacharacters, no paths), installs exactly one package, and never deletes
anything. health_check only reads and creates; it repairs nothing destructive.
A heal that fails is a dict, never a crash.
"""
from __future__ import annotations

import importlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent

_CORE = ["safety", "state", "budget", "models", "recipes", "proactive",
         "execution", "fastpath", "brain"]

# a PyPI distribution name: letters, digits, _ . -  and nothing else. This is the
# whole defence against ensure_module("foo; rm -rf ~") - the name is validated,
# never interpolated into a shell, and pip is invoked as an argv list.
_SAFE_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,60}")
_MISSING = re.compile(r"No module named ['\"]([A-Za-z0-9_][A-Za-z0-9_.\-]*)['\"]")


def _try_import(name: str) -> tuple[bool, str]:
    try:
        importlib.import_module(name)
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def missing_module(stderr: str) -> str | None:
    """The module name out of a 'No module named X' traceback, or None. Reads the
    TOP-LEVEL package, so a failed `import pandas.core` installs `pandas`."""
    m = _MISSING.search(stderr or "")
    return m.group(1).split(".")[0] if m else None


def ensure_module(name: str, timeout: int = 120) -> dict:
    """Make `name` importable, installing it from PyPI if missing. One attempt,
    bounded, validated. Returns a dict, never raises."""
    base = re.split(r"[.\[<>=!]", (name or "").strip())[0]
    if not _SAFE_NAME.fullmatch(base or ""):
        return {"ok": False, "error": f"unsafe module name {name!r}; not installed"}
    ok, _ = _try_import(base)
    if ok:
        return {"ok": True, "module": base, "already": True}
    try:
        p = subprocess.run([sys.executable, "-m", "pip", "install", base],
                           capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        return {"ok": False, "module": base, "error": f"pip failed: {e}"}
    importlib.invalidate_caches()
    ok, err = _try_import(base)
    return {"ok": ok, "module": base, "installed": ok,
            "error": "" if ok else (err or (p.stderr or "")[-300:])}


def health_check(db_path=None) -> dict:
    """Read-only (plus creating the workspace): what is working, what is not.
    Returns {ok, checks:[{name,ok,detail}], failed:[...], summary}."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:150]})

    for m in _CORE:
        ok, err = _try_import(m)
        add(f"import {m}", ok, err)

    try:
        import state
        state.set_fact("_healthcheck", f"{time.time():.0f}", db_path=db_path)
        add("database writable", True)
    except Exception as e:
        add("database writable", False, e)

    try:
        import safety
        safety.WORKSPACE.mkdir(parents=True, exist_ok=True)
        (safety.WORKSPACE / ".heal_probe").write_text("ok", encoding="utf-8")
        add("workspace writable", True, str(safety.WORKSPACE))
    except Exception as e:
        add("workspace writable", False, e)

    try:
        import models
        ok = bool(models.BRAIN and models.BRAIN_HEAVY and models.VISION)
        add("models configured", ok, f"{models.BRAIN} / {models.BRAIN_HEAVY}")
    except Exception as e:
        add("models configured", False, e)

    add("OPENAI_API_KEY present",
        bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
        "brain, eyes and delegate need it")

    try:
        import recipes
        add("recipes discoverable", True, f"{len(recipes.list())} saved")
    except Exception as e:
        add("recipes discoverable", False, e)

    failed = [c["name"] for c in checks if not c["ok"]]
    return {"ok": not failed, "checks": checks, "failed": failed,
            "summary": (f"all {len(checks)} checks passed" if not failed
                        else f"{len(failed)} of {len(checks)} FAILED: "
                             f"{', '.join(failed)}")}


def main() -> int:
    r = health_check()
    print(r["summary"])
    for c in r["checks"]:
        print(f"  [{'ok ' if c['ok'] else 'FAIL'}] {c['name']}"
              + (f"  - {c['detail']}" if c["detail"] else ""))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
