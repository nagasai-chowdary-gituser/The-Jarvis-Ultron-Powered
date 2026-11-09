"""
training.py — how Jarvis gets genuinely better at DOING, by doing.

This is the "serious training" the owner asked for. Not a model fine-tune, and
not a hand-written instruction sheet: a learn-by-doing loop. Every time Jarvis
finishes a real multi-step task that actually worked, it distills WHAT IT DID
into a structured, reusable PROCEDURE - the ordered steps, the tools, and the
specifics that worked on HIS machine - and stores it. The next time a similar
request comes, that proven procedure is handed back to it, so it repeats the way
that worked instead of re-discovering from scratch. Same task done again = the
procedure's use-count goes up and the latest working version is kept, so it gets
more reliable the more it is used.

This is stronger than the prose "lessons" in memory.py: a procedure is ordered,
counted, matched by the task it applies to, and injected as "you did this
before, N times, here are the exact steps - follow them". It sits ON TOP of the
seed knowledge in playbooks.py (hand-written expert starts) and grows past it.

Cost-guarded: it only tries to learn from turns that actually OPERATED something
(drove an app/site, made a file, messaged) over several steps - not every chat
line - so the one extra background model call is spent only where a reusable
skill could exist. Learning never blocks or breaks a reply (daemon thread, all
failures swallowed), and nothing injected or secret is ever stored.
"""
from __future__ import annotations

import json
import re
import threading

import models
import safety
import state

# Tools whose presence in a trace means Jarvis DID something in the world that is
# worth remembering how to repeat. A trace of only reads/looks is not a skill.
_OPERATING_TOOLS = (
    "open_chrome_profile", "open_app", "read_window", "read_text",
    "click_control", "click_sequence", "set_control", "cursor_click",
    "cursor_type", "cursor_key", "browser_navigate", "browser_click",
    "browser_type", "browser_evaluate", "ensure_browser", "make_pptx",
    "make_docx", "whatsapp_send", "email_draft", "email_send", "run_python",
    "save_skill",
)

_MIN_STEPS = 3                    # fewer than this is not a procedure worth saving
_MATCH_MIN = 2                    # trigger words that must appear to reuse a skill

_DISTILL_PROMPT = (
    "You are turning ONE successful run into a reusable PROCEDURE so the same "
    "kind of task is done the same proven way next time.\n"
    "You are given the user's request and the TOOL TRACE of what actually "
    "happened and worked. Produce a concrete, reusable procedure:\n"
    "- title: a short name for this kind of task (e.g. 'Make a PPT using "
    "ChatGPT').\n"
    "- trigger: 3-8 lowercase keywords that identify a future request of this "
    "kind (space-separated), so it can be matched.\n"
    "- steps: the ORDERED steps that worked, each one short and concrete, naming "
    "the tool and what it did - generalise the specifics so it reuses (e.g. "
    "'open_chrome_profile(main, chatgpt.com) - ONE tab', 'read_window to find "
    "the message box', 'set_control the full prompt', 'wait until the Stop "
    "button is gone', 'read_text to read the answer', 'make_pptx from it').\n"
    "- done_check: one line on how you KNOW it finished.\n"
    "Keep it to what genuinely transfers to the next run; drop one-off details. "
    "If the run was trivial or nothing reusable happened, return empty.\n"
    'Output ONLY JSON: {"title": "", "trigger": "", "steps": [], '
    '"done_check": ""}.')


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:80] or "procedure"


def worth_learning(trace: str) -> bool:
    """Only learn from a real multi-step OPERATING success."""
    t = (trace or "")
    if t.count("\n") + 1 < _MIN_STEPS:
        return False
    return any(tool in t for tool in _OPERATING_TOOLS)


def learn(user_text: str, reply: str, trace: str, source: str = "") -> None:
    """Distill a successful operating run into a procedure, off the critical path.

    Gated to real multi-step operating turns so the extra model call is rare.
    A daemon thread; every failure is swallowed - training must never cost a
    reply."""
    if not worth_learning(trace):
        return
    if safety.unsafe_to_learn(user_text) or safety.unsafe_to_learn(trace):
        return

    def work() -> None:
        try:
            from openai import OpenAI
            content = (f"USER REQUEST: {(user_text or '')[:600]}\n"
                       f"RESULT: {(reply or '')[:400]}\n\n"
                       f"TOOL TRACE (what worked):\n{trace[:1600]}\n")
            r = OpenAI(timeout=30, max_retries=1).responses.create(
                model=models.BRAIN, instructions=_DISTILL_PROMPT,
                input=[{"role": "user", "content": content}],
                reasoning={"effort": "low"}, max_output_tokens=600, store=False)
            models.record(r, models.BRAIN)
            raw = (r.output_text or "").strip()
            a, b = raw.find("{"), raw.rfind("}")
            obj = json.loads(raw[a:b + 1]) if (a >= 0 and b > a) else {}
            title = str(obj.get("title") or "").strip()
            steps = [str(s).strip() for s in (obj.get("steps") or [])
                     if str(s).strip()]
            trigger = str(obj.get("trigger") or "").strip().lower()
            done = str(obj.get("done_check") or "").strip()
            if not title or len(steps) < _MIN_STEPS:
                return                      # nothing reusable
            if safety.unsafe_to_learn(" ".join(steps)):
                return
            if not trigger:
                trigger = _slug(title).replace("-", " ")
            state.save_procedure(_slug(title), title, trigger, steps, done)
        except Exception as e:
            state.record_error("training.learn", e)

    threading.Thread(target=work, daemon=True, name="training-learn").start()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(w) > 2}


def match(text: str) -> dict | None:
    """The proven procedure that best fits this request, or None. Pure and free:
    keyword overlap between the request and each procedure's trigger."""
    tw = _words(text)
    if not tw:
        return None
    best, best_score = None, 0
    for p in state.procedures():
        trig = _words(p.get("trigger", "")) | _words(p.get("title", ""))
        score = len(trig & tw)
        # A flaky procedure (fails outweigh uses) is not trusted for reuse.
        if score >= _MATCH_MIN and score > best_score and \
                p.get("uses", 1) > p.get("fails", 0):
            best, best_score = p, score
    return best


def render(proc: dict | None) -> str:
    """Format a matched procedure as prompt-ready context, or '' for None."""
    if not proc:
        return ""
    steps = proc.get("steps") or []
    lines = "\n".join(f"{i + 1}) {s}" for i, s in enumerate(steps))
    done = f"\nDone when: {proc['done_check']}" if proc.get("done_check") else ""
    return (f"\nPROVEN PROCEDURE - you have done '{proc['title']}' successfully "
            f"{proc.get('uses', 1)} time(s). Do it the SAME proven way; adapt a "
            f"step only if it actually fails:\n{lines}{done}\n")


def block(text: str) -> str:
    """Prompt-ready proven procedure for this request, or '' when none matches.
    Injected only when there IS a learned way to do it, so ordinary turns pay
    nothing and the cached prompt stays identical."""
    return render(match(text))


def known() -> list[dict]:
    """What Jarvis has learned to do - for 'what can you do', newest-strongest
    first."""
    return [{"title": p["title"], "uses": p.get("uses", 1),
             "fails": p.get("fails", 0), "done_check": p.get("done_check", "")}
            for p in state.procedures()]
