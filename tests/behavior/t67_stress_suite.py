"""G67: twenty-plus varied real workloads all end on legitimate conditions.

Every one runs the real agent against the real machine. The shared acceptance
for all of them is the point of the whole repair:

    no task may end because of how many low-level actions it took

so each is checked for the banned message, for a named terminal condition, and
where it produces something, for that thing really existing on disk or in the
browser.
"""
import shutil
import sys
import time

import _harness as h

h.fresh_db()
import brain  # noqa: E402
import browser  # noqa: E402
import tasks  # noqa: E402
from safety import Session, Transport  # noqa: E402

LEGITIMATE = {"completed", "asked", "blocked", "cancelled", "timeout",
              "needs_auth", "stuck", ""}
BANNED = "ran out of steps"

results = []


def run(name, request, transport=Transport.DESK, check=None, budget_s=240):
    """One workload. `check(ws, reply)` proves the real outcome when there is one."""
    ws = h.workspace()
    b = brain.Brain(Session(transport), channel=f"chan:stress-{name}")
    t0 = time.perf_counter()
    try:
        reply = b.run(request.format(ws=ws.name))
        err = ""
    except Exception as e:
        reply, err = "", f"{type(e).__name__}: {e}"
    dt = time.perf_counter() - t0

    problems = []
    if err:
        problems.append(f"raised {err}")
    if BANNED in (reply or "").lower():
        problems.append("produced the step-exhaustion message")
    ended = b.metrics.get("ended", "")
    if ended not in LEGITIMATE:
        problems.append(f"unknown terminal condition {ended!r}")
    if ended == "runaway":
        problems.append("hit the runaway ceiling")
    if check and not err:
        try:
            ok, why = check(ws, reply or "")
            if not ok:
                problems.append(why)
        except Exception as e:
            problems.append(f"check raised {type(e).__name__}: {e}")

    results.append({
        "name": name, "seconds": round(dt, 1), "ended": ended,
        "rounds": b.metrics.get("rounds", 0), "calls": b.metrics["tool_calls"],
        "progress": b.metrics.get("progress", 0),
        "recoveries": b.metrics.get("recoveries", 0),
        "problems": problems,
    })
    shutil.rmtree(ws, ignore_errors=True)


def file_has(name, text=None, min_bytes=1):
    def check(ws, reply):
        p = ws / name
        if not p.is_file():
            return False, f"{name} was never created"
        body = p.read_text(encoding="utf-8", errors="replace")
        if len(body.encode()) < min_bytes:
            return False, f"{name} is only {len(body)} chars"
        if text and text.lower() not in body.lower():
            return False, f"{name} does not contain {text!r}"
        return True, ""
    return check


def reply_has(*words):
    """Any one of these words. Apostrophes are normalised first, because the
    model writes "couldn't" with a Unicode right single quote and a check
    looking for "could not" then calls a perfectly correct refusal a failure -
    which is exactly the sort of false alarm that wastes an investigation."""
    def check(ws, reply):
        low = (reply.lower().replace("’", "'").replace("n't", " not")
               .replace("“", '"').replace("”", '"'))
        if not any(w.lower() in low for w in words):
            return False, f"the reply mentions none of {words}: {reply[:90]!r}"
        return True, ""
    return check


# ── filesystem ───────────────────────────────────────────────────────────────
run("fs-write", "Write a file called a.txt in your workspace folder '{ws}' "
    "containing the word ALPHA.", check=file_has("a.txt", "ALPHA"))
run("fs-nested", "In your workspace folder '{ws}' create a folder called deep "
    "with a file inside it called b.txt containing BRAVO.",
    check=lambda ws, r: ((ws / "deep" / "b.txt").is_file(),
                         "deep/b.txt is missing"))
run("fs-count", "Count the files in your workspace folder '{ws}' and write "
    "that number into count.txt there.", check=file_has("count.txt"))
run("fs-search", "Make three .log files in your workspace folder '{ws}', put "
    "the word ERROR in exactly one of them, then tell me which one has it.",
    check=reply_has(".log", "error"))

# ── terminal ─────────────────────────────────────────────────────────────────
run("sh-run", "Run a command that prints the Python version and tell me what "
    "it printed.", check=reply_has("python", "3."))
run("sh-fail", "Run a command that is guaranteed to fail, and tell me its exit "
    "code and what it said.", check=reply_has("exit", "fail", "error", "not "))
run("sh-stderr", "Run a Python one-liner that writes BOOM to stderr and exits "
    "with code 3. Tell me both the exit code and the stderr.",
    check=reply_has("boom", "3"))

# ── system / desktop ─────────────────────────────────────────────────────────
run("sys-stats", "What is my CPU usage and how much disk is free?",
    check=reply_has("percent", "%", "gb"))
run("sys-os", "What version of Windows am I running and how many CPU cores?",
    check=reply_has("windows", "core"))
run("sys-procs", "How many processes are running right now?",
    check=reply_has("process", "running"))
run("sys-apps", "How many applications are installed on this machine?",
    check=reply_has("app", "install"))

# ── browser ──────────────────────────────────────────────────────────────────
run("web-nav", "Open example.com in the browser and tell me the exact page "
    "title.", check=reply_has("example domain"))
run("web-read", "Open example.com and save its visible text to page.txt in "
    "your workspace folder '{ws}'.", check=file_has("page.txt", min_bytes=50))
run("web-tabs", "How many browser tabs are open and what are their addresses?",
    check=reply_has("tab"))
run("web-dead", "Try to open https://this-host-does-not-exist-jarvis.invalid "
    "and tell me honestly whether it worked.",
    check=reply_has("not", "could not", "fail", "unable", "does not"))
run("web-dynamic", "Open music.youtube.com and tell me whether anything is "
    "playing right now.", check=reply_has("play", "not", "nothing", "no "))

# ── memory and state ─────────────────────────────────────────────────────────
run("mem-set", "Remember that my favourite colour is teal.",
    check=lambda ws, r: (any("teal" in str(v).lower()
                             for v in __import__("state").facts().values()),
                         "the fact was not stored"))
run("mem-get", "What is my favourite colour?", check=reply_has("teal"))
run("job-schedule", "Every ten minutes check whether Chrome is still running. "
    "Give me the job number.",
    check=lambda ws, r: (len(__import__("state").jobs("pending")) > 0,
                         "no job was actually queued"))

# ── multi-step and dependency ────────────────────────────────────────────────
run("multi-three", "Do three things in your workspace folder '{ws}': write "
    "x.txt containing 10, write y.txt containing 32, then write sum.txt "
    "containing the total of those two numbers.",
    check=file_has("sum.txt", "42"))
run("multi-chain", "In your workspace folder '{ws}' write seed.txt containing "
    "the number 7. Then read it back, multiply by 6, and write result.txt "
    "with the answer.", check=file_has("result.txt", "42"))
run("multi-mixed", "In your workspace folder '{ws}': find out how many CPU "
    "cores this machine has, then write that number into cores.txt, then read "
    "it back and tell me what it says.", check=file_has("cores.txt"))

# ── honesty and blockers ─────────────────────────────────────────────────────
run("blocked-impossible", "Read the file Z:/definitely/not/here.txt and tell "
    "me its contents.",
    check=reply_has("could not", "cannot", "blocked", "outside", "refus",
                    "denied", "does not exist", "no such"))
run("ambiguous", "Send it to him.", check=reply_has("who", "which", "what",
                                                    "?"))

# ── transports ───────────────────────────────────────────────────────────────
run("via-telegram", "What time is it?", transport=Transport.TELEGRAM,
    check=reply_has(":", "am", "pm"))
run("via-phone", "How much memory is free?", transport=Transport.PHONE,
    check=reply_has("gb", "percent", "memory"))

# ── report ───────────────────────────────────────────────────────────────────
bad = [r for r in results if r["problems"]]
banned_hits = [r for r in results
               if any("step-exhaustion" in p for p in r["problems"])]

print(f"# {len(results)} workloads run")
for r in results:
    mark = "FAIL" if r["problems"] else "ok  "
    print(f"#  {mark} {r['name']:20} {r['seconds']:6.1f}s "
          f"ended={r['ended'] or '-':10} rounds={r['rounds']:3d} "
          f"calls={r['calls']:3d} prog={r['progress']:3d}")
    for p in r["problems"]:
        print(f"#         -> {p}")

h.need(len(results) >= 20, f"only {len(results)} workloads ran; 20+ required")
h.need(not banned_hits,
       f"the step-exhaustion message appeared in: "
       f"{[r['name'] for r in banned_hits]}")
h.need(not bad,
       f"{len(bad)} of {len(results)} workloads failed: "
       f"{[(r['name'], r['problems'][0][:60]) for r in bad]}")

longest = max(results, key=lambda r: r["rounds"])
print(f"# deepest task: {longest['name']} at {longest['rounds']} rounds")

h.ok("STRESS_SUITE_OK")
