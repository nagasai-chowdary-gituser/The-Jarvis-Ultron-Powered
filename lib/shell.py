"""
lib/shell.py — run a command and know what actually happened.

A process starting is not a task succeeding, and that distinction is the whole
reason this file exists. Every call here returns the exit code, both streams
and the elapsed time, and nothing in it reports success on the strength of
having launched something.

WHAT IT GUARANTEES

    NO ORPHANS. A timeout kills the whole process TREE, not just the child
    that was waited on. `cmd /c npm test` spawns node underneath it; killing
    the shell alone leaves node running forever, holding a port and a file
    lock, and the next run then fails for a reason that has nothing to do with
    the code. psutil walks the tree and kills the leaves first.

    UTF-8, ALWAYS. Decoding with the Windows locale codec raised
    UnicodeDecodeError on any output containing an emoji or Telugu text - the
    command had already run correctly and only reading its output failed.

    CANCELLABLE. Given a task id, the child is registered with tasks.py before
    the wait begins, so cancelling the task ends the wait instead of blocking
    on it for the full timeout.
"""
from __future__ import annotations

import os
import subprocess
import time

DEFAULT_TIMEOUT_S = 60
OUTPUT_LIMIT = 20_000

CREATE_NO_WINDOW = 0x08000000


def _kill_tree(proc: subprocess.Popen) -> int:
    """Kill the process and everything it started. Returns how many died."""
    killed = 0
    try:
        import psutil
        parent = psutil.Process(proc.pid)
        kids = parent.children(recursive=True)
        for kid in kids:                      # leaves first, so nothing
            try:                              # re-parents and survives
                kid.kill()
                killed += 1
            except Exception:
                pass
        try:
            parent.kill()
            killed += 1
        except Exception:
            pass
        psutil.wait_procs(kids + [parent], timeout=5)
    except Exception:
        try:
            proc.kill()
            killed += 1
        except Exception:
            pass
    return killed


def run(command: str, cwd: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_S, task_id: int | None = None,
        shell: bool = True) -> dict:
    """Run a command. Returns what happened, never a bare True.

    `ok` means the process exited zero. It does NOT mean the task the command
    was supposed to accomplish succeeded - that is for the caller to verify
    against the real world, which is why stdout and stderr both come back."""
    if not (command or "").strip():
        raise ValueError("no command given")

    import safety
    safety.money_check(command)

    workdir = str(cwd) if cwd else str(safety.WORKSPACE)
    os.makedirs(workdir, exist_ok=True)

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    started = time.perf_counter()

    args = command if shell else command.split()
    proc = subprocess.Popen(
        args, shell=shell, cwd=workdir, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW)

    if task_id is not None:
        import tasks
        tasks.register_child(task_id, proc.pid)

    timed_out = False
    killed = 0
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        killed = _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:
            out, err = "", ""
    finally:
        if task_id is not None:
            import tasks
            tasks.register_child(task_id, None)

    elapsed = time.perf_counter() - started
    code = proc.returncode

    result = {
        "ok": (code == 0) and not timed_out,
        "exit_code": code,
        "stdout": (out or "")[:OUTPUT_LIMIT],
        "stderr": (err or "")[:OUTPUT_LIMIT],
        "seconds": round(elapsed, 3),
        "timed_out": timed_out,
        "command": command[:300],
        "cwd": workdir,
    }
    if timed_out:
        result["error"] = (f"timed out after {timeout:.0f}s; killed {killed} "
                           f"process(es) so nothing was left running")
    elif code != 0:
        # The reason, taken from what the command actually said. stderr is not
        # discarded here - ignoring it is how "it failed" becomes "it failed
        # for an unknown reason".
        tail = (err or out or "").strip().splitlines()
        result["error"] = (tail[-1][:300] if tail
                           else f"exited {code} with no output")
    return result


def python(code: str, **kw) -> dict:
    """Run a snippet with this interpreter. Used by checks and by recipes."""
    import sys
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(code)
        path = fh.name
    try:
        return run(f'"{sys.executable}" -u "{path}"', shell=True, **kw)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        return False


def kill(pid: int, tree: bool = True) -> dict:
    """End a process. Refuses to kill this process or its own parent."""
    import psutil
    pid = int(pid)
    if pid in (os.getpid(), os.getppid()):
        return {"ok": False, "error": "refusing to kill Jarvis itself"}
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return {"ok": False, "error": f"no process {pid}"}
    victims = (p.children(recursive=True) if tree else []) + [p]
    for v in victims:
        try:
            v.kill()
        except Exception:
            pass
    gone, alive_still = psutil.wait_procs(victims, timeout=5)
    return {"ok": not alive_still, "killed": len(gone),
            "survived": [x.pid for x in alive_still]}
