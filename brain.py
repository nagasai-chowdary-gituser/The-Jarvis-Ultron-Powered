"""
brain.py — one loop, four tools, and an honesty layer that is code.

WAS 1,906 LINES AND 39 TOOLS, costing 5,648 tokens of JSON schema on every
single request whether or not one of them was used. Nine of those tools were
added in the last few days, each to fix a symptom, and each one made tool
selection worse for every request that followed. The build got dumber because
it got bigger.

Four tools now. Capability lives in lib/ as importable Python and costs nothing
until it is imported; LIB.md indexes it in ~680 tokens. Anthropic's measured
version of the same move: 150,000 tokens of definitions down to 2,000.

THE HONESTY LAYER IS THE POINT OF THIS FILE. The worst thing the old build did
was not being slow:

    "I sent 'hi'... and I'm monitoring for his replies until he says bye"

Nothing was monitoring. The turn ended. There was no background worker, so the
sentence was not an error - it was a description of something that did not
exist. And once one report is invented, every other report is worthless.

Prompt wording cannot fix that, because the thing producing the wording is the
thing that is wrong. So it is enforced structurally, below, in `vet()`:

  - "monitoring" may only appear if schedule_job returned an id THIS TURN
  - a completed-action claim must trace to a tool result
  - every report carries a source tag, and an unlabelled guess is rejected
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

import budget as budget_mod
import execution
import fastpath
import models
import safety
import state
from safety import Blocked, Session, Transport

ROOT = Path(__file__).parent
LIB_INDEX = (ROOT / "lib" / "LIB.md")

# MAX_ITERATIONS IS GONE. It was 12, and falling out of `for _ in range(12)`
# printed "I ran out of steps before finishing that." - a legitimate objective
# refused because of an integer that knew nothing about the work. Termination
# now comes from budget.Budget, which stops on a reason that is TRUE about the
# task: completed, blocked, cancelled, timed out, stuck, or a runaway ceiling
# that means a bug rather than a long task.
TOOL_OUTPUT_LIMIT = 2000          # every tool return, before it enters context
EXEC_TIMEOUT_S = 60


# ─────────────────────────────────────────────────────────────────────────────
# The prompt. Role + rules + LIB.md + this contact's state.
# The first three are byte-identical every request, so the prefix caches.
# ─────────────────────────────────────────────────────────────────────────────

ROLE = """You are Jarvis, a personal agent running on your owner's own Windows machine. He is the only person you take instructions from, and you call him Boss.

YOU ARE JARVIS. You are not ChatGPT, not Claude, not an assistant "helping a friend". If anyone asks who you are - on WhatsApp, on the phone, anywhere - you are Jarvis, his assistant. Never name the model you run on, and never claim to be a different product.

HOW YOU WORK
You have four tools. Everything else you do by writing Python with run_python, which can import the library indexed below. If you need a capability that is not in the library, write the code for it - do not ask for a new tool.

WHAT COUNTS AS TRUE
Say what a tool RETURNED, never what you intended. A message is sent when send() returned a message id. A file is written when write() returned its size. If a tool failed, say it failed and why - never substitute a plausible success. If you did not read something, you do not know it.

If the Boss says you are wrong, call recall FIRST and read what is actually stored before you answer. In a real exchange the old build argued with him, then immediately did the thing it had just said was unnecessary. Check, then speak.

HOW YOU REACH HIM WHEN HE HAS NOT ASKED
A job can message him or ring his phone (params.urgency='call'). Ringing is for things that cannot wait. You are limited to three calls a day, none between eleven at night and seven in the morning unless the job is critical, and nothing unprompted within twenty minutes of the last time you disturbed him - anything held back is folded into the next message, never dropped. A monitor REPORTS; it never fixes, spends, or sends anything on its own. It tells him, and he decides.

ANYTHING THAT WAITS IS A JOB
You never wait. No sleep loops, no "I'll keep an eye on it". If something has to be watched, polled, or carried on until a condition, call schedule_job - it returns a job id instantly and a separate worker process does the waiting. You may only use the words "monitoring", "watching" or "keeping an eye" when schedule_job gave you an id in this same turn.

ASK ONCE, THEN ACT
ask_boss is cheaper than being wrong - use it when a name is ambiguous, when a number could be one of two people, or when an instruction could mean two things. But ask ONCE. The conversation so far is above; if you already asked and he answered, that answer stands - do not ask again in different words. "Yes", "same one", "proceed" and "go ahead" are answers to YOUR LAST QUESTION, so read it and act. Asking a man the same thing three times is worse than guessing, because it wastes his time and still does not do the job.

FINISH THE WHOLE REQUEST
Three things asked means three things done. Do not stop between parts to check in. If one part is genuinely impossible, do the rest and then say which part failed and why.

HOW YOU SPEAK
Short. One or two sentences. No markdown, no bullet points, no emoji - it is often read aloud. Numbers as a person would say them."""


def system_prompt(jid: str = "") -> str:
    """Role + rules + LIB.md + state. The cached prefix is everything but state."""
    lib = LIB_INDEX.read_text(encoding="utf-8") if LIB_INDEX.exists() else ""
    parts = [ROLE, "\n\nWHAT YOU CAN IMPORT\n", lib, state.facts_block()]
    if jid:
        parts.append(state.as_context(jid))
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# THE FOUR TOOLS. Adding a fifth is a decision for the Boss, not a bug fix.
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "name": "run_python",
        "description": (
            "Run Python. This is the general tool - it does anything. `lib/` is "
            "importable (see the index in your prompt). New subprocess each "
            "time, 60s limit, workspace as cwd; print what you need to see. "
            "Works from the desk, the phone and Telegram alike."),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "why": {"type": "string",
                        "description": "One line: what this is for."},
            },
            "required": ["code", "why"],
        },
    },
    {
        "type": "function",
        "name": "schedule_job",
        "description": (
            "Hand work to the background worker. Returns a job id IMMEDIATELY - "
            "it does not wait. This is the ONLY way to do anything that waits, "
            "polls, watches, or continues until a condition. kinds: watch_chat "
            "(params: contact), reminder (params: text), poll_url (params: url, "
            "selector), check_process (params: name). Add params.urgency='call' "
            "to have it RING THE BOSS instead of messaging him - use that only "
            "when it genuinely cannot wait. Add params.critical=true to bypass "
            "the quiet hours and the 20-minute gap between interruptions; that "
            "is for things like the disk filling or the site being down."),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string",
                         "enum": ["watch_chat", "reminder", "poll_url",
                                  "check_process"]},
                "params": {"type": "object"},
                "every_seconds": {"type": "integer"},
                "until": {"type": "string",
                          "description": "Stop condition in plain words, e.g. "
                                         "'he says bye'."},
            },
            "required": ["kind", "params"],
        },
    },
    {
        "type": "function",
        "name": "recall",
        "description": (
            "Read what is actually stored: a person's history and unresolved "
            "intents, your commitments, a job's status, saved facts. Call this "
            "BEFORE answering anything about a person or a past action, and "
            "always before disagreeing with the Boss."),
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "type": "function",
        "name": "ask_boss",
        "description": (
            "Ask instead of guessing. Cheaper than being wrong. Use it when a "
            "contact name is ambiguous or an instruction could mean two "
            "things."),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["question"],
        },
    },
]


def schema_tokens() -> int:
    return len(json.dumps(TOOLS)) // 4


# ─────────────────────────────────────────────────────────────────────────────
# THE HONESTY LAYER — code, not prompt wording
# ─────────────────────────────────────────────────────────────────────────────

# A CLAIM BY JARVIS that it is watching something, not any mention of the
# words. The old pattern matched bare "watch"/"monitor" anywhere, so Jarvis
# saying "the live WATCH panel is empty" got mangled into "the live-(not
# monitoring - no job was created) panel" - it rewrote a true statement about
# the UI into nonsense. The honesty rule only cares about a FIRST-PERSON
# promise to keep observing, so a first-person subject must sit just before the
# verb (or one of the few phrases that are a promise on their own).
WAITING_WORDS = re.compile(
    r"\b(?:i(?:'|\s?a)?m|i'?ll|i\s+will|i(?:'|\s?ha)?ve(?:\s+been)?|i\s+am)\b"
    r"[^.?!]{0,24}?\b(?:monitor(?:ing)?|watch(?:ing)?|keep(?:ing)?\s+an?\s+eye|"
    r"look(?:ing)?\s+out|let\s+you\s+know)\b"
    r"|\b(?:in\s+the\s+background|until\s+(?:he|she|they)\s+says?)\b", re.I)

DONE_WORDS = re.compile(
    r"\b(sent|delivered|messaged|texted|called|created|wrote|saved|opened|"
    r"done|completed)\b", re.I)

SOURCE_TAGS = ("[whatsapp]", "[state]", "[guess]", "[telegram]", "[phone]",
               "[desk]", "[web]", "[file]")


_CLASS_SOUP = re.compile(r"(?:\b[a-z]\w{5,}\b[ \t]+){4,}")
_HTML_TAG = re.compile(r"<[^>]{20,}>")


def human_error(text: str, limit: int = 140) -> str:
    """Turn a library traceback into one sentence a person can act on.

    THIS EXISTS BECAUSE OF WHAT THE BOSS SAW ON TELEGRAM:

        That did NOT happen. xdj266r x14z9mp xat24cr x1lziwak xexx8yu ...

    A Playwright click timed out, and Playwright's error message helpfully
    includes a dump of the element it resolved - which on WhatsApp means forty
    obfuscated CSS class names. That went through the failure path into vet()
    and straight out to a human as the explanation for why his task failed.

    Nobody can do anything with that. An error the owner cannot act on is the
    same as no error at all, except it also makes the system look broken."""
    t = (text or "").strip()
    if not t:
        return "it failed"
    t = t.split("Call log:")[0]                 # Playwright's DOM dump
    t = _HTML_TAG.sub(" ", t)                   # any inline HTML
    t = _CLASS_SOUP.sub(" ", t)                 # runs of class-name tokens
    # A Python traceback puts the exception LAST, so read from the end. An
    # earlier version stripped each line before testing its indentation, which
    # made every source line look like a top-level one - so it reported
    # "who = open(contact)" as the reason a message did not send.
    lines = [ln for ln in t.splitlines() if ln.strip()]
    for raw in reversed(lines):
        if raw.startswith((" ", "\t")) or raw.lstrip().startswith(("File \"",
                                                                   "Traceback")):
            continue
        t = raw.strip()
        break
    t = " ".join(t.split())
    t = re.sub(r"\b[\w.]*_impl[\w.]*\.", "", t)      # playwright._impl._errors.
    t = re.sub(r"^\w+(\.\w+)+:\s*", "", t)           # any other module path

    # The common ones, said the way a person would say them.
    low = t.lower()
    if "timeout" in low:
        t = ("the app did not respond in time - it may be minimised, still "
             "loading, or busy")
    elif "notsignedin" in low.replace(" ", ""):
        t = "WhatsApp is showing the QR screen and needs to be signed in"
    elif "notrunning" in low.replace(" ", ""):
        t = "WhatsApp is not open"
    elif low.startswith("wrongchat") or "matches" in low:
        t = t.split(":", 1)[-1].strip() or t
    return t[:limit] or "it failed"


def vet(reply: str, evidence: dict) -> str:
    """Rewrite a reply that claims more than the turn earned.

    `evidence` is what actually happened this turn:
        job_ids   ids returned by schedule_job
        results   tool results that succeeded
        failures  tool results that did not

    THIS IS THE FIX FOR THE WORST BUG IN THE OLD BUILD. "I'm monitoring for his
    replies" was produced by a model that had scheduled nothing, because there
    was nothing to schedule. Wording cannot police wording, so the sentence is
    checked against the turn's actual tool results before it leaves."""
    text = (reply or "").strip()
    if not text:
        return text

    jobs = evidence.get("job_ids") or []
    ok_results = evidence.get("results") or []
    failures = evidence.get("failures") or []

    # 1. A promise to keep watching requires a job id FROM THIS TURN.
    if WAITING_WORDS.search(text):
        if jobs:
            tag = " ".join(f"[job #{j}]" for j in jobs)
            if "[job #" not in text:
                text = f"{text} {tag}"
        else:
            text = WAITING_WORDS.sub("(not monitoring - no job was created)", text)
            text += (" I did NOT set anything running: nothing is watching. "
                     "Ask me to schedule it if you want that.")

    # 2. A completed-action claim with no successful tool result behind it.
    if DONE_WORDS.search(text) and not ok_results:
        if failures:
            why = human_error(str(failures[0].get("error")
                                  or failures[0].get("stderr") or ""))
            # Tagged here, because a contradiction is the one thing Jarvis is
            # certain about - labelling it "[guess]" downstream read as though
            # it were unsure whether the failure had failed.
            text = f"[state] That did NOT happen. {why}"
        else:
            # Nothing failed because NOTHING RAN. This is the exact shape of
            # the worst lie the old build told - "I sent it" with no send
            # behind it - so it gets contradicted, not softened to a guess.
            text = ("That did NOT happen. No tool ran this turn, so nothing "
                    "was actually done. What I was going to say was: " + text)

    # 3. Everything carries a source. An unlabelled guess is not allowed.
    if not any(t in text.lower() for t in SOURCE_TAGS) and "[job #" not in text:
        text = ("[state] " if ok_results else "[guess] ") + text
    return text


# ─────────────────────────────────────────────────────────────────────────────
# run_python
# ─────────────────────────────────────────────────────────────────────────────

def run_python(code: str, why: str = "") -> dict:
    """A fresh subprocess with lib/ importable and the workspace as cwd."""
    safety.money_check(code)
    safety.refuse_destructive_code(code)      # deletion & runtime-built code
    safety.WORKSPACE.mkdir(parents=True, exist_ok=True)
    script = safety.WORKSPACE / f".run_{int(time.time()*1000)}.py"
    header = (f"import sys\nsys.path.insert(0, r'{ROOT}')\n"
              f"sys.path.insert(0, r'{ROOT / 'lib'}')\n")
    script.write_text(header + code, encoding="utf-8")
    try:
        # UTF-8, EXPLICITLY. text=True alone decodes with the Windows locale
        # codec (cp1252 here), so printing a WhatsApp contact name crashed the
        # tool with UnicodeDecodeError - "Clip Tool🐸", any Telugu message, any
        # emoji at all. The code ran fine; reading its output is what failed.
        # PYTHONIOENCODING makes the child encode as UTF-8 to match.
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        p = subprocess.run([sys.executable, "-u", str(script)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env,
                           timeout=EXEC_TIMEOUT_S, cwd=str(safety.WORKSPACE))
        out = {"ok": p.returncode == 0,
               "stdout": (p.stdout or "")[:TOOL_OUTPUT_LIMIT],
               "stderr": (p.stderr or "")[-TOOL_OUTPUT_LIMIT:],
               "exit_code": p.returncode}
        if p.returncode != 0:
            out["error"] = (p.stderr or "non-zero exit")[-400:]
        return out
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timed out after {EXEC_TIMEOUT_S}s",
                "hint": "do less in one step, or schedule_job it"}
    finally:
        script.unlink(missing_ok=True)


def do_recall(topic: str) -> dict:
    """Read state. The thing that kills the contradiction bug."""
    t = (topic or "").strip()
    found: dict = {"topic": t}
    contact = state.find_contact(t)
    if contact:
        s = state.load(contact["jid"])
        found.update({"contact": contact["name"] or contact["jid"],
                      "jid": contact["jid"],
                      "summary": s["summary"],
                      "open_intents": s["open_intents"],
                      "recent": s["messages"][-8:]})
    found["commitments"] = state.open_commitments(t if contact else "")[:8]
    found["facts"] = state.facts()
    found["jobs"] = [{"id": j["id"], "kind": j["kind"], "status": j["status"]}
                     for j in state.jobs("pending")[:8]]
    return {"ok": True, **found}


# ─────────────────────────────────────────────────────────────────────────────
# The loop
# ─────────────────────────────────────────────────────────────────────────────

class Brain:
    """One turn, start to finish. No agent logic lives anywhere else."""

    def __init__(self, session: Session, *, jid: str = "", emit=None,
                 channel: str = "", client: OpenAI | None = None):
        self.session = session
        self.jid = jid
        self.emit = emit or (lambda ev: None)
        self.client = client or OpenAI(timeout=45.0, max_retries=1)
        # THE CONVERSATION SURVIVES THE TURN.
        #
        # This used to start empty every time, and main.py builds a new Brain
        # for every incoming message - so each Telegram line arrived with no
        # history whatsoever. The result was a loop the Boss had to watch
        # happen to him:
        #
        #     Jarvis: "Which chat - Clip Tool or +91 91234 56789?"
        #     Boss:   "Same number"
        #     Jarvis: "Got it, +91 91234 56789. What message should I send?"
        #     Boss:   "Yes proceed"
        #     Jarvis: "Boss, what would you like me to proceed with?"
        #
        # It had already asked, already been answered, and could not see
        # either. Prior turns are loaded from SQLite, so continuity survives a
        # restart of the process as well as the turn.
        self.channel = channel or (jid or session.transport.value)
        self.convo: list[dict] = _recent_turns(self.channel)
        self.evidence: dict = {"job_ids": [], "results": [], "failures": []}
        self.pending_question: dict | None = None
        # §27: measured, not estimated. A bottleneck you cannot see is a
        # bottleneck you will keep paying for.
        self.metrics: dict = {"tool_calls": 0, "model_calls": 0,
                              "parallel_peak": 1, "saved_seconds": 0.0,
                              "fastpath": False, "seconds": 0.0,
                              "ended": "", "rounds": 0, "actions": 0,
                              "recoveries": 0, "progress": 0}
        self.task_id: int | None = None
        self.budget = None

    def _say(self, ev: dict) -> None:
        try:
            self.emit(ev)
        except Exception as e:
            # The HUD not listening must never break a turn, but a display
            # that has silently stopped receiving is worth knowing about.
            state.record_error("brain.emit", e)

    def dispatch(self, name: str, args: dict) -> dict:
        """Every tool call goes through here. One gate, one audit point."""
        sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)[:200]}"
        try:
            safety.check(name, self.session, sig)
        except Blocked as b:
            return {"ok": False, "status": "blocked", "error": b.reason,
                    "spoken": b.spoken}
        self.session.calls += 1
        self.session.note(sig)

        # The HUD is the live audit log: the full source of every run_python.
        self._say({"type": "tool", "name": name,
                   "code": args.get("code", ""),
                   "why": args.get("why", "") or args.get("topic", "")})

        try:
            if name == "run_python":
                r = run_python(args.get("code", ""), args.get("why", ""))
            elif name == "schedule_job":
                jid_ = state.add_job(args.get("kind", ""), args.get("params") or {},
                                     args.get("every_seconds") or 30,
                                     args.get("until"))
                self.evidence["job_ids"].append(jid_)
                r = {"ok": True, "job_id": jid_,
                     "note": f"job #{jid_} is running in the worker process"}
            elif name == "recall":
                r = do_recall(args.get("topic", ""))
            elif name == "ask_boss":
                self.pending_question = {"question": args.get("question", ""),
                                         "options": args.get("options") or []}
                r = {"ok": True, "asked": args.get("question", "")}
            else:
                r = {"ok": False, "error": f"no tool named {name!r}"}
        except Blocked as b:
            r = {"ok": False, "status": "blocked", "error": b.reason}
        except Exception as e:
            r = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        (self.evidence["results"] if r.get("ok")
         else self.evidence["failures"]).append(r)

        # WHAT THIS CALL ACTUALLY ACHIEVED, if anything.
        #
        # A tool call is not progress. A tool call that CHANGED SOMETHING or
        # FOUND SOMETHING is. Deciding that here, from the real return value,
        # is what lets the engine tell "still getting somewhere" apart from
        # "going round in circles" - which the old step counter could not do.
        if self.budget is not None:
            outcome = "ok" if r.get("ok") else str(r.get("error"))[:80]
            repeats = self.budget.loops.note(name, args, outcome)

            if r.get("ok"):
                kind = _progress_kind(name, args, r)
                if kind:
                    self.budget.progress.record(kind, str(r)[:150])
                    self.budget.did(productive=True)
                else:
                    self.budget.did(productive=False)   # a look, not a change
            else:
                # A failure is a recovery attempt, from its OWN purse.
                self.budget.recovered()
                if repeats >= self.budget.loops.limit:
                    self._say({"type": "loop", "tool": name, "repeats": repeats})
        self._say({"type": "tool_result", "name": name, "ok": bool(r.get("ok")),
                   "detail": str(r.get("stdout") or r.get("error") or "")[:400]})
        return r

    def run(self, text: str) -> str:
        """One user turn. Returns what to say. Never raises."""
        # THE FAST PATH. "Open Chrome" is not a reasoning problem, and a
        # reasoning model answering it spends five seconds to do eighty
        # milliseconds of work. Unmistakable requests are executed directly
        # and answered from a template; anything with a hint of ambiguity
        # falls through to the model below.
        _remember_turn(self.channel, "in", text)

        quick = fastpath.try_run(text)
        if quick is not None:
            self._say({"type": "route", "model": "fastpath", "effort": "none"})
            self._say({"type": "tool", "name": "fastpath",
                       "code": str(quick.get("evidence", ""))[:300],
                       "why": text[:80]})
            self._say({"type": "tool_result", "name": "fastpath", "ok": True,
                       "detail": str(quick.get("evidence", ""))[:200]})
            self.evidence["results"].append({"ok": True, **quick})
            reply = f"[state] {quick['say']}"
            self._say({"type": "reply", "text": reply})
            self.metrics["fastpath"] = True
            self.metrics["seconds"] = quick.get("seconds", 0.0)
            _remember_turn(self.channel, "out", reply)
            return reply

        self.convo.append({"role": "user", "content": text})
        prompt = system_prompt(self.jid)
        self._say({"type": "memory", "turns": len(self.convo) - 1})
        model, effort = models.pick(text)
        self._say({"type": "route", "model": model, "effort": effort})
        turn_started = time.perf_counter()

        bud = budget_mod.for_request(text, task_id=self.task_id)
        self.budget = bud
        self._say({"type": "budget", "deadline": bud.deadline_s})

        while True:
            verdict = bud.verdict()
            if verdict.terminal:
                out = vet(bud.explain(verdict), self.evidence)
                self.metrics.update(bud.snapshot())
                self.metrics["ended"] = verdict.value
                self._say({"type": "ended", "verdict": verdict.value,
                           **bud.snapshot()})
                _remember_turn(self.channel, "out", out)
                return out
            bud.round_started()

            try:
                resp = self.client.responses.create(
                    model=model, instructions=prompt, input=self.convo,
                    tools=TOOLS, reasoning={"effort": effort},
                    max_output_tokens=models.MAX_OUTPUT_TOKENS, store=False)
            except Exception as e:
                # The message, not just the class. Reporting only
                # "BadRequestError" hid a one-word schema bug for a whole
                # session - the class name alone is never enough to fix it.
                self._say({"type": "error", "error": f"{type(e).__name__}: {e}"})
                return (f"[guess] I could not reach the model. "
                        f"{type(e).__name__}: {str(e)[:300]}")

            self.metrics["model_calls"] += 1
            models.record(resp, model)
            for item in resp.output:
                self.convo.append(_echo(item))

            calls = [o for o in resp.output
                     if getattr(o, "type", None) == "function_call"]
            if not calls:
                # The model has stopped asking for tools, which means it
                # considers the objective answered. That is COMPLETED - a real
                # terminal condition - and it is recorded as one so the metrics
                # say why the turn ended rather than leaving it blank.
                reply = vet((resp.output_text or "").strip(), self.evidence)
                self._say({"type": "reply", "text": reply})
                self.metrics.update(bud.snapshot())
                self.metrics["ended"] = budget_mod.Verdict.COMPLETED.value
                self._say({"type": "ended", "verdict": "completed",
                           **bud.snapshot()})
                _remember_turn(self.channel, "out", reply)
                return reply or "[guess] Done."

            # PARALLEL. The model already asks for several tools at once; this
            # used to run them one after another, so four independent lookups
            # cost the sum of four waits for no reason. execution.run_calls
            # runs them together and serialises only the ones that claim the
            # same resource - two WhatsApp sends would otherwise interleave
            # keystrokes into whichever chat happened to be open.
            batch = []
            for fc in calls:
                try:
                    args = json.loads(fc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                batch.append(execution.Call(id=fc.call_id,
                                            tool=getattr(fc, "name", ""),
                                            args=args))

            report = execution.run_calls(batch, self.dispatch)
            self.metrics["tool_calls"] += report.calls
            self.metrics["parallel_peak"] = max(self.metrics["parallel_peak"],
                                                report.widest)
            self.metrics["saved_seconds"] += report.saved_seconds
            if report.widest > 1:
                self._say({"type": "parallel", "calls": report.calls,
                           "widest": report.widest,
                           "saved": round(report.saved_seconds, 2)})

            for c in batch:
                self.convo.append({
                    "type": "function_call_output", "call_id": c.id,
                    "output": json.dumps(c.result or {},
                                         default=str)[:TOOL_OUTPUT_LIMIT]})

            if self.pending_question:
                q = self.pending_question["question"]
                opts = self.pending_question["options"]
                asked = vet(q + (f" ({' / '.join(opts)})" if opts else ""),
                            self.evidence)
                self.metrics.update(bud.snapshot())
                self.metrics["ended"] = "asked"
                _remember_turn(self.channel, "out", asked)
                return asked




TURN_MEMORY = 12          # how many previous lines the model sees


def _recent_turns(channel: str) -> list[dict]:
    """The last few exchanges on this channel, as plain input items.

    ONLY plain text is persisted, never the tool-call items. Reasoning and
    function_call entries carry ids that are valid for exactly one request, so
    replaying them raises; and the conversation only needs to remember WHAT
    WAS SAID, because whatever the tools found is already in the answer that
    was given."""
    if not channel:
        return []
    try:
        rows = state.history(channel, TURN_MEMORY)
    except Exception:
        return []
    out = []
    for r in rows:
        text = (r.get("text") or "").strip()
        if not text:
            continue
        out.append({"role": "user" if r.get("direction") == "in" else "assistant",
                    "content": text[:1500]})
    return out


def _remember_turn(channel: str, direction: str, text: str) -> None:
    if not (channel and (text or "").strip()):
        return
    try:
        state.add_message(channel, direction, text)
    except Exception as e:
        # Losing a turn silently is how the conversation quietly forgets.
        state.record_error("brain.remember_turn", e)


def _progress_kind(tool: str, args: dict, result: dict) -> str | None:
    """Did this result move the objective, and how? None means it did not.

    Deliberately strict. Reading a value that was already known, or listing
    something and finding it unchanged, is an OBSERVATION - useful, but not a
    reason to believe the task is advancing. Counting those as progress is how
    an agent convinces itself it is busy while standing still."""
    if not result.get("ok"):
        return None
    if tool == "schedule_job":
        return "subgoal"
    if tool == "recall":
        return "discovery" if str(result.get("found") or result.get("text") or "") else None
    if tool == "ask_boss":
        return None                      # asking is not advancing
    if tool != "run_python":
        return None

    out = str(result.get("stdout") or "")
    code = str(args.get("code") or "")
    if not out.strip():
        return None                      # ran, printed nothing, proved nothing

    # Something now exists, or the machine is in a different state.
    if any(w in code for w in ("files.write", "files.append", "files.mkdir",
                               "files.move", "files.copy", "whatsapp.send",
                               "phone.message", "state.set_fact")):
        return "artifact"
    if any(w in code for w in ("open_app", "goto", "click", "fill", "press",
                               "media(", "kill(", "shell.run", "start_app")):
        return "state_change"
    return "discovery"                   # it printed something new


def _echo(item):
    """Echo an output item back as input, in the shape the API accepts.

    by_alias AND exclude_none are both load-bearing, and this cost a live
    turn to find. A plain model_dump() emits the SDK's python-safe field
    names, so `async` comes back as `async_`, along with `caller` and
    `namespace` set to None - and the API rejects the whole request:

        400 Unknown parameter: 'input[1].async_'. Did you mean 'async'?

    which meant every turn that called a tool died on its SECOND model call,
    after the tool had already run. by_alias restores the wire names;
    exclude_none drops the fields that are absent rather than null."""
    if not hasattr(item, "model_dump"):
        return dict(item)
    d = item.model_dump(by_alias=True, exclude_none=True)
    d.pop("status", None)          # echoed back, the API refuses it
    return d
