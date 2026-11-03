"""
delegate.py — hand the hard part to a stronger tool, then JUDGE the result.

THIS IS THE DIFFERENCE BETWEEN AN AGENT AND A RELAY. A relay forwards a prompt to
ChatGPT and pastes back whatever comes out. An agent decides the job is worth a
heavier tool, drives that tool in the Boss's own browser, then LOOKS at what it
made, decides if it is actually good, and if not says exactly what to fix and has
it done again - looping until the result meets the bar, and only then bringing it
back. That review-and-fix loop is the whole value; without it, delegation is just
a more expensive way to be wrong.

    produce(task, prompt, target="chatgpt", max_cycles=3, success_check=...)

Jarvis chooses this path on its own when a job has to be GOOD (an important deck,
a serious document, a hard problem). Small or routine work it just does itself.

WHO DOES THE JUDGING. Not the tool that produced the work - that grades its own
homework. The judge is a SEPARATE model call (the strong brain) comparing the
output against the Boss's actual request and the success check. That independence
is the point (grounded review beats intrinsic self-critique).

HONESTY ABOUT FRAGILITY. The browser steps drive live ChatGPT / Claude pages, and
those DOMs change. The selectors below are best-effort with generic fallbacks;
the first real run on the Boss's machine is where they get confirmed, and every
step returns what it actually observed so a wrong selector surfaces as a clear
failure rather than a silent wrong answer. The JUDGING, which is the part that
makes this worth doing, is model-driven and does not depend on any selector.

SAFETY. This only ever drives sites the Boss is already signed into, in his own
Chrome, and it sends nothing except the prompt text he asked to be produced. It
does not log in, does not touch credentials, and goes online only because the job
in hand needs it - which is exactly the line in the system prompt.
"""
from __future__ import annotations

import json
import time

import models

# Per-target page knowledge. Candidate selectors are tried in order; the first
# that a page actually exposes wins, and read() falls back to the whole page.
_SITES = {
    "chatgpt": {
        "url": "https://chatgpt.com/",
        "input": ["#prompt-textarea", "div[contenteditable='true']", "textarea"],
        "answer": ["[data-message-author-role='assistant']", ".markdown",
                   "main"],
        "busy": ["[data-testid='stop-button']", "button[aria-label='Stop streaming']"],
        "name": "ChatGPT",
    },
    "claude": {
        "url": "https://claude.ai/new",
        "input": ["div[contenteditable='true'].ProseMirror",
                  "div[contenteditable='true']"],
        "answer": [".font-claude-message", "[data-testid='message-content']",
                   "main"],
        "busy": ["button[aria-label='Stop response']"],
        "name": "Claude",
    },
}


def _site(target: str) -> dict:
    return _SITES.get((target or "").lower().strip(), _SITES["chatgpt"])


# ─────────────────────────────────────────────────────────────────────────────
# The judge — a SEPARATE model call, not the tool that made the work
# ─────────────────────────────────────────────────────────────────────────────

def judge(task: str, success_check: str, output: str) -> dict:
    """Is this output good enough to give the Boss? Returns
    {good: bool, fixes: str, reason: str}. Never raises - a judge that crashes
    must not strand the loop, so on any error it asks for one more pass."""
    bar = success_check or ("it fully and correctly satisfies what the Boss "
                            "asked for, with nothing missing or wrong")
    instructions = (
        "You are a strict reviewer working for the Boss. Compare the OUTPUT "
        "against the TASK and the SUCCESS CHECK. Be honest and specific. Reply "
        "with ONLY a JSON object: {\"good\": true|false, \"fixes\": \"the exact "
        "changes needed, as an instruction to whoever produced it, empty if "
        "good\", \"reason\": \"one line\"}. Mark good=false if anything real is "
        "missing, wrong, shallow, or off-brief.")
    payload = (f"TASK:\n{task}\n\nSUCCESS CHECK:\n{bar}\n\n"
               f"OUTPUT:\n{(output or '')[:6000]}")
    try:
        from openai import OpenAI
        client = OpenAI(timeout=45.0, max_retries=1)
        resp = client.responses.create(
            model=models.BRAIN_HEAVY, instructions=instructions,
            input=payload, reasoning={"effort": "low"},
            max_output_tokens=500, store=False)
        models.record(resp, models.BRAIN_HEAVY)
        raw = (resp.output_text or "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start:end + 1]) if start >= 0 else {}
        return {"good": bool(data.get("good")),
                "fixes": str(data.get("fixes") or "").strip(),
                "reason": str(data.get("reason") or "").strip()}
    except Exception as e:
        # Fail toward another pass rather than passing unreviewed work through.
        return {"good": False, "fixes": "", "reason": f"judge failed: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Driving the live page (best-effort; returns what it observed)
# ─────────────────────────────────────────────────────────────────────────────

def _fill_first(browser, selectors, text, submit):
    for sel in selectors:
        try:
            r = browser.fill(sel, text, submit=submit)
            if r.get("ok"):
                return {"ok": True, "selector": sel}
        except Exception:
            continue
    return {"ok": False, "error": "no input box matched any known selector"}


def _read_answer(browser, site, max_chars=6000):
    for sel in site["answer"]:
        try:
            r = browser.read(sel, max_chars=max_chars)
            txt = (r.get("text") or r.get("content") or "").strip()
            if txt:
                return txt
        except Exception:
            continue
    try:
        return (browser.read("", max_chars=max_chars).get("text") or "").strip()
    except Exception:
        return ""


def _wait_until_done(browser, site, timeout=90, settle=2.5):
    """Wait for generation to finish: the answer text stops growing. Returns the
    final text. Polling the TEXT means it works even when the 'stop' button
    selector is stale."""
    deadline = time.time() + timeout
    last, stable_since = "", time.time()
    while time.time() < deadline:
        cur = _read_answer(browser, site)
        if cur and cur == last:
            if time.time() - stable_since >= settle:
                return cur
        else:
            last, stable_since = cur, time.time()
        time.sleep(1.2)
    return last


# ─────────────────────────────────────────────────────────────────────────────
# The loop
# ─────────────────────────────────────────────────────────────────────────────

def produce(task: str, prompt: str, target: str = "chatgpt", max_cycles: int = 3,
            success_check: str = "", look: bool = True) -> dict:
    """Have a stronger tool produce something, review it, and iterate until it is
    good or the cycles run out. Returns a full record; never raises.

    task          what the Boss actually wants (used by the judge)
    prompt        the opening prompt to send to the tool
    target        'chatgpt' or 'claude' - a site the Boss is already signed into
    max_cycles    how many produce-review rounds at most (he sets the ceiling)
    success_check the bar the judge holds the output to
    look          also glance at the screen with the eyes after the first answer
    """
    site = _site(target)
    record = {"ok": False, "target": site["name"], "cycles": 0,
              "final_text": "", "transcript": [], "verdict": None}
    try:
        import browser
    except Exception as e:
        record["error"] = f"the browser is not available: {e}"
        return record

    try:
        browser.goto(site["url"])
    except Exception as e:
        record["error"] = f"could not open {site['name']}: {e}"
        return record
    time.sleep(2.0)

    sent = _fill_first(browser, site["input"], prompt, submit=True)
    if not sent.get("ok"):
        record["error"] = (f"could not find {site['name']}'s input box - its "
                           f"page may have changed, or it is not signed in")
        return record

    answer = _wait_until_done(browser, site)
    record["transcript"].append({"cycle": 1, "sent": prompt[:500],
                                 "got": answer[:1500]})
    record["cycles"] = 1
    record["final_text"] = answer

    if look:
        try:
            from lib import vision
            seen = vision.see_screen(
                f"Does the screen show a complete answer to: {task[:160]}? "
                f"Read any obvious problems.", window=site["name"])
            if seen.get("ok"):
                record.setdefault("eyes", []).append(seen.get("answer"))
        except Exception:
            pass

    verdict = judge(task, success_check, answer)
    record["verdict"] = verdict

    while not verdict["good"] and record["cycles"] < max_cycles:
        fixes = verdict["fixes"] or ("Improve it: it does not yet fully meet "
                                     "what was asked. Be more complete and exact.")
        follow = _fill_first(browser, site["input"], fixes, submit=True)
        if not follow.get("ok"):
            record["error"] = "could not send the fix request back to the chat"
            break
        answer = _wait_until_done(browser, site)
        record["cycles"] += 1
        record["final_text"] = answer
        record["transcript"].append({"cycle": record["cycles"],
                                     "sent": fixes[:500], "got": answer[:1500]})
        verdict = judge(task, success_check, answer)
        record["verdict"] = verdict

    record["ok"] = bool(verdict and verdict.get("good"))
    if not record["ok"] and "error" not in record:
        record["note"] = (f"stopped after {record['cycles']} cycle(s) without "
                          f"meeting the bar; returning the best version so far")
    return record
