"""
Shared scaffolding for the behavioural checks.

Every script here runs against the REAL modules and the REAL machine. The only
thing faked is the database path, so a check cannot corrupt the live task list
or read another check's rows - isolation of test data, never of behaviour.

A check prints its token only at the very end, after every assertion. `ok()`
is therefore the single place a success marker can come from, and a partial or
crashed run cannot produce one.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

# UTF-8 OUT, ALWAYS. These checks print real-world text - a track title, a
# contact name, a path - and the gate runner's stdout is the Windows locale
# codec (cp1252 here). A check whose assertions had ALL passed then died on its
# final print() with UnicodeEncodeError, and the gate recorded a failure for a
# capability that works. Reconfiguring the stream is the fix; sanitising every
# print would only move the trap.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# A PIN FOR THE TESTS, NOT THE OWNER'S. The checks used to authenticate with
# the real PIN as a literal, so the credential was written into seven files
# that get committed. This is set AFTER load_dotenv on purpose - it overrides
# the real value for this process only, so the checks prove the same behaviour
# without the repository knowing anything that unlocks the actual machine.
TEST_PIN = "4731"
os.environ["JARVIS_PIN"] = TEST_PIN

_tmp: Path | None = None


def fresh_db() -> Path:
    """Point state and tasks at an empty database for this process only."""
    global _tmp
    import state
    import tasks
    _tmp = Path(tempfile.mkdtemp(prefix="jarvis-check-"))
    db = _tmp / "t.db"
    # BOTH, and the environment variable is the one that matters: run_python
    # spawns a child, and a child inherits the environment but not our module
    # attributes. Without this the agent writes to the real database while the
    # check reads an empty one, and a correct agent looks broken.
    os.environ["JARVIS_DB"] = str(db)
    state.DB_PATH = db
    if hasattr(state, "_READY"):
        state._READY = set()
    tasks.reset_for_tests()
    return db


def workspace() -> Path:
    """A scratch directory inside the real workspace, cleaned on the way out."""
    import safety
    d = safety.WORKSPACE / f".check-{int(time.time()*1000)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup() -> None:
    if _tmp and _tmp.exists():
        shutil.rmtree(_tmp, ignore_errors=True)


def need(condition: bool, message: str) -> None:
    """An assertion that says what was actually wrong, not just `False`."""
    if not condition:
        raise AssertionError(message)


def ok(token: str) -> None:
    """The ONLY place a success token is printed. Reached only after every
    assertion above it has passed."""
    cleanup()
    print(token)


def fail(message: str) -> None:
    cleanup()
    print(f"CHECK FAILED: {message}", file=sys.stderr)
    raise SystemExit(1)
