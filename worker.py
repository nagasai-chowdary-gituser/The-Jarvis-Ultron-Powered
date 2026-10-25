"""
worker.py — the process that waits. Run it beside Jarvis, not inside it.

    python worker.py

THE LIE THIS EXISTS TO MAKE TRUE:

    "I sent 'hi', kept the conversation friendly, and I'm monitoring for his
     replies until he says bye."

Nothing was monitoring. The turn ended, the loop returned, and the sentence
described a background process that did not exist anywhere in the codebase.
That is worse than being slow, because once one report is invented every other
report is worth nothing.

A turn cannot wait. A turn is a request/response cycle that has to end, and
anything told to "carry on until he says bye" either blocks the whole assistant
or stops the moment it returns. So waiting moved OUT: the brain calls
schedule_job, gets an id back in milliseconds, and this process does the
waiting in its own time.

Jobs live in SQLite, so they survive a restart of either process. That is also
why the id is real - the Boss can ask what is running, and something can answer.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import safety  # noqa: E402
import state  # noqa: E402
from safety import Session, Transport  # noqa: E402

TICK_S = 3.0                  # how often the queue is read (was 10 - too slow:
                              # a watched-chat reply waited a whole tick before
                              # the worker even looked, which is most of the
                              # "it takes 15 seconds to reply" the owner saw)
WATCH_INTERVAL_S = 5          # how often a watched chat is re-checked
MAX_JOB_AGE_S = 6 * 3600      # a watch that has run this long has been forgotten
LOG = ROOT / "worker.log"


def log(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    msg = f"{stamp} {line}"
    # The console may be a cp1252 stream; a ₹, an emoji or a non-Latin name in
    # a log line would then raise UnicodeEncodeError and - because log() is
    # called from the awareness/reply paths - take the whole pulse down with it.
    # Never let a print kill the worker.
    try:
        print(msg, flush=True)
    except Exception:
        try:
            print(msg.encode("ascii", "replace").decode(), flush=True)
        except Exception:
            pass
    try:
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# REACHING THE BOSS — the part that decides whether to interrupt a human
#
# A monitor that fires every time it notices something becomes noise, and a
# noisy channel gets muted - which silences the one alert that actually
# mattered. So every unprompted contact passes the same gate:
#
#     COOLDOWN     nothing unprompted within 20 minutes of the last thing,
#                  unless the job is marked critical
#     QUIET HOURS  no calls between 23:00 and 07:00, unless critical
#     DAILY CAP    at most 3 outbound calls a day
#
# NOTHING IS EVER SILENTLY DROPPED. A suppressed note is spooled and folded
# into the next message that is allowed through, or into the morning brief. An
# agent that quietly bins information it decided you did not need is exactly
# the dishonesty the rest of this build exists to prevent.
# ─────────────────────────────────────────────────────────────────────────────

COOLDOWN_S = 20 * 60
MAX_CALLS_PER_DAY = 3
QUIET_FROM, QUIET_TO = 23, 7          # local hours; no calls in between
SPOOL_KEY = "spooled_notes"


def _in_quiet_hours(now: float | None = None) -> bool:
    h = time.localtime(now or time.time()).tm_hour
    return h >= QUIET_FROM or h < QUIET_TO


def _may_disturb(critical: bool = False) -> bool:
    """Has enough time passed since the Boss was last interrupted?"""
    if critical:
        return True
    return (time.time() - state.last_outreach_at()) >= COOLDOWN_S


def _clean(note: str) -> str:
    """Strip library tracebacks out of anything bound for a human.

    A spooled note once held a full Playwright timeout - element HTML, forty
    obfuscated class names and all - and the spool is folded into the NEXT
    message, so that wall of text was queued to be sent to the Boss. vet()
    cleans the reply it returns; nothing was cleaning what got stored."""
    try:
        import brain
        return brain.human_error(note, limit=220) if (
            "Traceback" in note or "Call log:" in note or "locator" in note
        ) else note
    except Exception:
        return note


def _spool(note: str) -> None:
    """Hold a suppressed note until there is a legitimate moment to send it."""
    note = _clean(note)
    try:
        held = json.loads(state.facts().get(SPOOL_KEY) or "[]")
    except Exception:
        held = []
    held.append({"ts": time.time(), "note": note[:400]})
    state.set_fact(SPOOL_KEY, json.dumps(held[-40:]))
    log(f"held back (cooldown): {note[:90]}")


def _take_spool() -> list[str]:
    try:
        held = json.loads(state.facts().get(SPOOL_KEY) or "[]")
    except Exception:
        held = []
    if held:
        state.set_fact(SPOOL_KEY, "[]")
    return [h.get("note", "") for h in held if h.get("note")]


def tell_boss(text: str, job_id: int | None = None, critical: bool = False,
              force: bool = False) -> bool:
    """Telegram the Boss. Returns whether it actually went.

    `force` is for replies to something he just did - those are not
    unprompted, so the cooldown does not apply to them."""
    if not (force or _may_disturb(critical)):
        _spool(text)
        return False
    held = _take_spool()
    if held:
        text = text + "\n\nAlso, held back while I was staying quiet:\n" + \
               "\n".join(f"- {h}" for h in held)
    text = _clean(text)
    try:
        from lib import phone
        phone.message(text)
        state.log_outreach("telegram", text[:200], job_id, critical)
        return True
    except Exception as e:
        log(f"could not reach the Boss: {type(e).__name__}: {e}")
        state.record_error("worker.tell_boss", e, fatal=True)
        return False


def call_boss(reason: str, job_id: int | None = None,
              critical: bool = False) -> dict:
    """Ring the Boss and talk to him. Degrades to Telegram, never to silence.

    The call itself is lib.phone.call_me, which points Twilio at the SAME
    /voice webhook an inbound call hits - so he answers into a real
    conversation with full context, not a recording. realtime.py is reused
    exactly as it is; none of it needed changing to be dialled from here."""
    if _in_quiet_hours() and not critical:
        note = f"(would have called, but it is quiet hours) {reason}"
        sent = tell_boss(note, job_id, critical)
        return {"called": False, "why": "quiet hours", "telegram": sent}

    if state.calls_today() >= MAX_CALLS_PER_DAY and not critical:
        note = f"(would have called, but that is {MAX_CALLS_PER_DAY} today) {reason}"
        sent = tell_boss(note, job_id, critical)
        return {"called": False, "why": "daily call cap", "telegram": sent}

    if not _may_disturb(critical):
        _spool(f"(wanted to call) {reason}")
        return {"called": False, "why": "cooldown", "telegram": False}

    try:
        from lib import phone
        r = phone.call_me(reason)
        state.log_outreach("call", reason, job_id, critical)
        log(f"called the Boss about job #{job_id}: {reason[:80]} -> {r.get('sid')}")
        return {"called": True, "sid": r.get("sid")}
    except Exception as e:
        # The call failed; the INFORMATION still has to arrive.
        log(f"call failed ({type(e).__name__}: {e}) - falling back to Telegram")
        sent = tell_boss(f"(tried to call, it failed) {reason}", job_id, critical)
        return {"called": False, "why": f"{type(e).__name__}", "telegram": sent}


def _deliver(report: str, job_id: int, urgency: str = "telegram",
             critical: bool = False) -> None:
    """One place where a job's finding becomes an interruption."""
    text = f"[job #{job_id}] {report}"
    if urgency == "call":
        call_boss(text, job_id, critical)
    else:
        tell_boss(text, job_id, critical)


# ─────────────────────────────────────────────────────────────────────────────
# Job kinds
# ─────────────────────────────────────────────────────────────────────────────

def _until_met(until: str, text: str) -> bool:
    """Has the stop condition been met?

    Deliberately literal. "he says bye" is checked by looking for the word, not
    by asking a model - a model call per incoming message to decide whether to
    stop would cost more than the conversation."""
    u = (until or "").lower()
    t = (text or "").lower()
    if not u:
        return False
    for word in ("bye", "goodbye", "gtg", "talk later", "good night",
                 "see you", "ttyl"):
        if word in u and word in t:
            return True
    key = u.replace("he says", "").replace("she says", "").strip(" '\"")
    return bool(key) and key in t


def watch_chat(job: dict) -> dict:
    """Poll one chat and REPLY ON OUR OWN when something arrives.

    This is the whole feature. The Boss says "talk to him until he says bye",
    goes quiet, and the replies keep happening because this loop is running."""
    from lib import whatsapp

    params = job["params"]
    contact = params.get("contact") or params.get("jid") or ""
    since = float(params.get("since") or job["created_at"])
    persona = params.get("persona", "")

    try:
        jid = whatsapp.open(contact)["contact"]
    except Exception as e:
        return {"stop": True, "note": f"cannot resolve {contact!r}: {e}"}

    try:
        fresh = whatsapp.new_since(jid, since)
    except Exception as e:
        log(f"job #{job['id']} bridge down: {e}")
        return {"stop": False, "note": "bridge unreachable"}

    if not fresh:
        return {"stop": False}

    for msg in fresh:
        text = msg["text"]
        state.add_message(jid, "in", text)
        log(f"job #{job['id']} <- {contact}: {text[:70]}")

        if _until_met(job.get("until") or "", text):
            tell_boss(f"[job #{job['id']}] {contact} said: {text[:120]}\n"
                      f"That is the stop condition, so I have finished watching.")
            return {"stop": True, "note": f"stop condition met: {text[:80]}"}

        reply, alert = _reply_and_judge(jid, contact, text, persona)

        # INTELLIGENCE: if this message is something the Boss must know now -
        # money he owes/took, a demand for payment, a call/meeting request, a
        # decision only he can make - tell him. Rule: CALL first; call_boss
        # falls back to a Telegram message if the call cannot be placed.
        if alert:
            log(f"job #{job['id']} URGENT from {contact}: {alert[:90]}")
            call_boss(alert, job_id=job["id"], critical=True)

        if not reply:
            continue
        try:
            mid = whatsapp.send(jid, reply)["contact"]
            log(f"job #{job['id']} -> {contact}: {reply[:70]} [{mid}]")
        except Exception as e:
            log(f"job #{job['id']} SEND FAILED: {e}")
            tell_boss(f"[job #{job['id']}] I could not reply to {contact}: {e}")
            return {"stop": False, "note": "send failed"}

    state.add_job  # keep the import honest
    params["since"] = fresh[-1]["ts"]
    _save_params(job["id"], params)
    return {"stop": False}


# What makes a message worth interrupting the Boss for. Deliberately described
# as JUDGMENT, not a keyword list - the owner asked for "human-like
# intelligence": a sharp assistant decides, it does not pattern-match.
_URGENCY_RULES = """
ALSO act as the Boss's sharp personal assistant and decide whether THIS message is something he must be told RIGHT NOW. Use judgment like a smart human would - these are the kinds of things that matter:
- money: someone says he owes or took money, demands a payment, names an amount he must pay or is owed.
- a request to reach him: asking for a call, a video call, a Google Meet, a meeting, to come somewhere, or to talk urgently.
- a decision only he can make: an approval, a yes/no, a question you cannot answer for him, a commitment someone wants.
- genuinely time-sensitive: a real deadline, "now/asap/urgent/today", an emergency.
Ordinary chit-chat, greetings, small talk and things you can simply handle are NOT urgent. When it IS urgent, write one short line for the Boss naming WHO and WHAT, as a person would say it aloud - e.g. "Kapcut says you took his 10,000 and wants it back now" or "Priya is asking for a video call about the project"."""


def _reply_and_judge(jid: str, contact: str, incoming: str,
                     persona: str) -> tuple[str, str]:
    """ONE model call that both replies AND judges urgency.

    Returns (reply, alert). `alert` is a short line for the Boss when the
    message is something he must know now, else "". Doing both in one call
    keeps a watched chat cheap and fast - no second round trip per message.

    IDENTITY IS ANCHORED HERE (the old build once replied "I'm ChatGPT..."),
    and the urgency judgment is what the owner means by intelligence: Jarvis
    noticing "he's asking about money you took" and telling him, like a person."""
    import json as _json

    import models
    from openai import OpenAI

    identity = _persona_text(persona)
    ctx = state.as_context(jid)
    fenced = safety.fence(incoming, f"a WhatsApp message from {contact}")

    prior = []
    try:
        for m in state.history(jid, 10):
            who = "You" if m.get("direction") == "out" else contact
            prior.append(f"{who}: {(m.get('text') or '')[:200]}")
    except Exception:
        pass
    thread = ("\n\nTHE CONVERSATION SO FAR (oldest first):\n" + "\n".join(prior)
              if prior else "")

    try:
        client = OpenAI(timeout=30.0, max_retries=1)
        r = client.responses.create(
            model=models.BRAIN,
            instructions=identity + ctx + _URGENCY_RULES,
            input=[{"role": "user", "content":
                    f"{thread}\n\n{fenced}\n\nReply to continue the "
                    f"conversation in one or two short sentences, the way a "
                    f"person texts - do not start over or re-introduce "
                    f"yourself. Then judge urgency.\n\nOutput ONLY JSON: "
                    f'{{"reply": "<your reply>", "urgent": true|false, '
                    f'"alert": "<one line for the Boss naming who and what, or '
                    f'empty>"}}'}],
            reasoning={"effort": "low"},
            max_output_tokens=500, store=False)
        models.record(r, models.BRAIN)
        raw = (r.output_text or "").strip()
    except Exception as e:
        log(f"reply generation failed: {type(e).__name__}: {e}")
        return "", ""

    reply, alert = "", ""
    try:
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            obj = _json.loads(raw[a:b + 1])
            reply = str(obj.get("reply", "")).strip()
            if obj.get("urgent") and str(obj.get("alert", "")).strip():
                alert = f"{contact} on WhatsApp: {str(obj['alert']).strip()}"
        else:
            reply = raw                  # no JSON - the whole thing is the reply
    except Exception:
        reply = raw                      # malformed JSON - fall back to the text
    if not reply:
        reply = raw                      # never drop the message silently

    if reply:
        state.add_message(jid, "out", reply)
        state.update_thread(jid, summary=(state.thread(jid)["summary"] or "")[:400])
    return reply[:900], alert[:300]


def _persona_text(override: str = "") -> str:
    """The identity anchor, from ONE file so it cannot drift."""
    if override:
        return override
    p = ROOT / "persona.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ("You are Jarvis, replying on your owner's WhatsApp on his behalf. "
            "You are NOT ChatGPT and not any other product. Never say which "
            "model you are.")


def reminder(job: dict) -> dict:
    """A reminder is something HE asked for, so it is not an interruption."""
    text = job["params"].get("text", "reminder")
    tell_boss(f"[job #{job['id']}] {text}", job["id"], force=True)
    return {"stop": True, "note": "delivered"}


def poll_url(job: dict) -> dict:
    """Read one value off a page, repeatedly. Free - no model call."""
    p = job["params"]
    try:
        import browser
        r = browser.probe(p.get("url", ""), selector=p.get("selector", ""))
        value = (r.get("text") or "").strip()
    except Exception as e:
        return {"stop": False, "note": f"read failed: {type(e).__name__}"}
    last = p.get("last")
    if value and value != last:
        p["last"] = value
        _save_params(job["id"], p)
        # It REPORTS. It does not decide to ring anyone - tick() does that.
        return {"stop": False,
                "report": f"{p.get('url','')} now reads: {value[:120]}"}
    return {"stop": False}


def check_process(job: dict) -> dict:
    from lib import desktop
    name = job["params"].get("name", "")
    running = bool(desktop.processes(name))
    was = job["params"].get("was")
    report = ""
    if was is not None and running != was:
        report = f"{name} is now {'running' if running else 'NOT running'}"
    p = dict(job["params"]); p["was"] = running
    _save_params(job["id"], p)
    return {"stop": False, "report": report}


def agent_task(job: dict) -> dict:
    """Run a whole request through the full orchestrator, in the background.

    This is what makes "research X and send me a report" a thing Jarvis can be
    left to do: the request was captured in an authorised turn, and here the
    same terra brain - specialists, MCP, memory and all - carries it out while
    nobody is at the keyboard, then hands the finished answer back to the Boss.

    One-shot by design. terra is not cheap, so a background task does its work
    once and reports; it does not re-run every tick. Anything that genuinely
    must repeat until a condition is a watch_chat or a poll, which are cheap."""
    import agent_core
    req = (job["params"].get("request") or job["params"].get("task") or "").strip()
    if not req:
        return {"stop": True, "note": "agent_task had no request"}
    try:
        reply = agent_core.run_turn(req, Transport.WORKER, channel="chan:worker")
        return {"stop": True, "report": reply, "note": "agent task done"}
    except Exception as e:
        log(f"job #{job['id']} agent_task crashed: {traceback.format_exc()[-400:]}")
        return {"stop": True,
                "report": f"I could not finish that background task: "
                          f"{type(e).__name__}",
                "note": f"{type(e).__name__}: {e}"}


MISSION_MAX_S = 2 * 3600          # a mission runs at most this long, then reports
MISSION_RETRIES = 3               # consecutive failed advances before giving up


def mission(job: dict) -> dict:
    """A ROBUST long-running job: a goal pursued over many steps, across hours
    and across restarts, with checkpointing and recovery.

    This is what agent_task is not. agent_task runs the brain once; a mission
    runs it REPEATEDLY, each time advancing toward the goal from the progress so
    far, until it is DONE, it is BLOCKED, a stop condition is met, or the time
    runs out. "research X deeply and write me a full report", "keep watching Y
    and handle it until Z", "work through this big list" - all one engine.

    - CHECKPOINT: progress is written to the job row every advance, so a worker
      restart resumes exactly where it was (the job is still pending).
    - RECOVER: a failed advance is retried; after MISSION_RETRIES in a row it
      stops and reports honestly rather than spinning forever. Any real progress
      resets the counter.
    - HONEST END: done / blocked / stop-condition / out-of-time each end it with
      a clear report, never a silent stall."""
    import agent_core
    params = job["params"]
    goal = (params.get("goal") or params.get("request") or "").strip()
    if not goal:
        return {"stop": True, "note": "mission had no goal"}
    progress = params.get("progress", "")
    fails = int(params.get("fails", 0))
    until = (job.get("until") or "").strip()

    prompt = (
        f"MISSION GOAL: {goal}\n\n"
        f"PROGRESS SO FAR:\n{progress or '(nothing yet - just starting)'}\n\n"
        "Advance the mission now: do the next useful steps toward the goal with "
        "your tools. Then end your reply with exactly one tag:\n"
        "  DONE: <the result / deliverable> - if the goal is fully achieved.\n"
        "  BLOCKED: <why, and what you need> - if you cannot continue.\n"
        "  PROGRESS: <what you advanced this step> - if there is more to do.")

    try:
        reply = agent_core.run_turn(prompt, Transport.WORKER,
                                    channel=f"chan:mission:{job['id']}")
    except Exception as e:
        fails += 1
        params["fails"] = fails
        _save_params(job["id"], params)
        log(f"mission #{job['id']} advance failed ({fails}): {e}")
        if fails >= MISSION_RETRIES:
            return {"stop": True, "note": f"{type(e).__name__}: {e}",
                    "report": f"I had to stop the mission '{goal[:70]}' - it "
                              f"failed {fails} times. Latest: {type(e).__name__}."}
        return {"stop": False}               # retry next tick

    low = reply.lower()
    if "done:" in low:
        result = reply[low.index("done:") + 5:].strip() or reply
        return {"stop": True, "note": "done",
                "report": f"Mission done - {goal[:50]}: {result[:400]}"}
    if "blocked:" in low:
        why = reply[low.index("blocked:") + 8:].strip() or reply
        return {"stop": True, "note": "blocked",
                "report": f"I'm blocked on '{goal[:50]}': {why[:300]}"}

    # Progress: checkpoint it, reset the failure counter, keep going.
    params["progress"] = (progress + "\n" + reply)[-3500:]
    params["fails"] = 0
    _save_params(job["id"], params)

    if until and _until_met(until, reply):
        return {"stop": True, "note": "stop condition met",
                "report": f"Mission '{goal[:50]}' reached its stop condition: "
                          f"{reply[:200]}"}
    if time.time() - (job.get("created_at") or 0) > MISSION_MAX_S:
        return {"stop": True, "note": "out of time",
                "report": f"I ran the full time on '{goal[:50]}'. Where it got "
                          f"to: {reply[:250]}"}
    return {"stop": False}                    # advance again next tick


KINDS = {"watch_chat": watch_chat, "reminder": reminder,
         "poll_url": poll_url, "check_process": check_process,
         "agent_task": agent_task, "mission": mission}

# MONITORS ARE READ-ONLY, AND THAT IS STRUCTURAL RATHER THAN A RULE.
#
# These two kinds detect and report. They cannot message, call, spend, appeal
# or "fix" anything, because the code that reaches the Boss lives OUTSIDE them
# - they return a string, and tick() decides what to do with it. A monitor
# that could act on its own finding is a monitor that can be wrong at 3am with
# nobody watching.
#
# watch_chat is deliberately NOT in this set. It is a conversation responder,
# not a monitor: replying on its own is the entire reason it exists. The
# distinction is worth keeping sharp, because they look similar and only one
# of them is allowed to act.
MONITOR_KINDS = {"poll_url", "check_process"}


def _save_params(job_id: int, params: dict) -> None:
    c = state._conn()
    try:
        c.execute("UPDATE jobs SET params=? WHERE id=?",
                  (json.dumps(params), job_id))
        c.commit()
    finally:
        c.close()


# ─────────────────────────────────────────────────────────────────────────────
# The loop
# ─────────────────────────────────────────────────────────────────────────────

def tick() -> int:
    done = 0
    for job in state.due_jobs():
        fn = KINDS.get(job["kind"])
        if fn is None:
            state.finish_job(job["id"], f"unknown kind {job['kind']!r}", "failed")
            continue
        if time.time() - (job["created_at"] or 0) > MAX_JOB_AGE_S:
            state.finish_job(job["id"], "ran past its maximum age", "expired")
            tell_boss(f"[job #{job['id']}] stopped - it had been running "
                      f"for hours.", job["id"])
            continue
        try:
            out = fn(job)
        except Exception as e:
            log(f"job #{job['id']} crashed: {traceback.format_exc()[-400:]}")
            state.finish_job(job["id"], f"{type(e).__name__}: {e}", "failed")
            continue
        # The single place a finding becomes an interruption. Monitors never
        # reach the Boss themselves; they hand a sentence up to here.
        report = (out.get("report") or "").strip()
        if report:
            _deliver(report, job["id"],
                     urgency=job["params"].get("urgency", "telegram"),
                     critical=bool(job["params"].get("critical")))

        if out.get("stop"):
            state.finish_job(job["id"], out.get("note", ""), "done")
            log(f"job #{job['id']} finished: {out.get('note','')}")
        else:
            state.reschedule(job["id"])
        done += 1
    return done


# ─────────────────────────────────────────────────────────────────────────────
# The morning brief — one message a day, never a call
#
# Built into the loop rather than scheduled as a job on purpose. A brief that
# depends on the model having remembered to schedule it is a brief that stops
# arriving the first time a turn goes wrong.
# ─────────────────────────────────────────────────────────────────────────────

BRIEF_HOUR = 7
BRIEF_MINUTE = 30
BRIEF_KEY = "last_brief_day"


def _brief_text() -> str:
    day_ago = time.time() - 24 * 3600
    lines = []

    moved = state.threads_moved_since(day_ago)
    if moved:
        lines.append("Moved since yesterday:")
        for th in moved[:8]:
            who = th.get("name") or (th.get("jid") or "").split("@")[0]
            lines.append(f"- {who}: {(th.get('summary') or '')[:140]}")

    open_c = state.open_commitments()
    if open_c:
        lines.append("")
        lines.append("Still open:")
        for c in open_c[:8]:
            who = f" ({c['who']})" if c.get("who") else ""
            due = f" - due {c['due']}" if c.get("due") else ""
            lines.append(f"- {c['what'][:120]}{who}{due}")

    held = _take_spool()
    if held:
        lines.append("")
        lines.append("Held back overnight:")
        lines += [f"- {h}" for h in held[:10]]

    if not lines:
        return ""
    spend = state.spend_today()
    lines.append("")
    lines.append(f"Yesterday cost ${spend:.2f}.")
    return "Morning, Boss.\n\n" + "\n".join(lines)


def maybe_brief() -> bool:
    """Fire once a day, after BRIEF_HOUR:BRIEF_MINUTE local. Telegram only."""
    now = time.localtime()
    today = time.strftime("%Y-%m-%d", now)
    if state.facts().get(BRIEF_KEY) == today:
        return False
    if (now.tm_hour, now.tm_min) < (BRIEF_HOUR, BRIEF_MINUTE):
        return False
    text = _brief_text()
    state.set_fact(BRIEF_KEY, today)        # set even when empty: once a day
    if not text:
        log("morning brief: nothing moved, nothing sent")
        return False
    # force=True: this is a fixed daily appointment he asked for, not a
    # monitor deciding on its own that now is a good moment.
    tell_boss(text, force=True)
    log("morning brief sent")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# The awareness loop — initiative, the thing that makes Jarvis a PA not a tool
#
# Every few minutes Jarvis stops and asks itself, like a sharp assistant who is
# paying attention all day: "given everything I know about the Boss's world
# right now, is there anything he should be told, or that I should do?" Most of
# the time the answer is nothing, and it stays quiet. When something genuinely
# matters - an overdue promise, a conversation that needs his reply, something
# due today - it tells him, through the SAME cooldown / quiet-hours / daily-cap
# gate everything else uses, so initiative never becomes noise.
#
# It watches only what the owner has already consented to see (his commitments,
# his running jobs, conversations that changed) - not a background scrape of
# every chat, which he said he does not want.
# ─────────────────────────────────────────────────────────────────────────────

AWARENESS_EVERY_S = 10 * 60
AWARENESS_KEY = "last_awareness_ts"
AWARENESS_LAST_MSG = "last_awareness_msg"

_AWARENESS_PROMPT = """You are Jarvis, your owner's proactive personal assistant. Below is the state of his world right now. Decide, like a sharp human PA who is paying attention, whether there is anything he should be TOLD or that needs his attention THIS moment.

Surface ONLY what genuinely matters: a commitment that is overdue or due today, a decision only he can make, something clearly time-sensitive, a conversation waiting on his reply, a promise about to be broken. Do NOT surface routine things, anything already handled, or anything you told him recently. Most of the time the right answer is to stay quiet.

Call him (urgent=true) only when it truly cannot wait; otherwise send a quiet message. Speak as a person would, naming who and what.

Output ONLY JSON: {"act": true|false, "urgent": true|false, "message": "<one or two short lines for the Boss, or empty>"}"""


def _world_snapshot() -> str:
    """What Jarvis can see of the Boss's world right now, in a few lines."""
    lines = [time.strftime("Now: %A %d %B %Y, %H:%M", time.localtime())]
    try:
        oc = state.open_commitments()
    except Exception:
        oc = []
    if oc:
        lines.append("Open commitments:")
        for c in oc[:10]:
            who = f" ({c['who']})" if c.get("who") else ""
            due = f" - due {c['due']}" if c.get("due") else ""
            lines.append(f"- {c['what'][:120]}{who}{due}")
    try:
        jb = state.jobs("pending")
    except Exception:
        jb = []
    if jb:
        lines.append("Running jobs: "
                     + ", ".join(f"#{j['id']} {j['kind']}" for j in jb[:8]))
    try:
        moved = state.threads_moved_since(time.time() - 6 * 3600)
    except Exception:
        moved = []
    if moved:
        lines.append("Conversations that changed recently:")
        for th in moved[:6]:
            who = th.get("name") or (th.get("jid") or "").split("@")[0]
            lines.append(f"- {who}: {(th.get('summary') or '')[:120]}")

    # His calendar and inbox, once he has connected Google. These are what make
    # the awareness loop a real PA rather than a WhatsApp watcher.
    try:
        from lib import google
        if google.is_setup():
            cal = google.calendar_upcoming(hours=12)
            if cal.get("ok") and cal.get("events"):
                lines.append("Upcoming on your calendar:")
                for ev in cal["events"][:6]:
                    lines.append(f"- {ev['start']}: {ev['summary']}")
            mail = google.gmail_unread(6)
            if mail.get("ok") and mail.get("unread"):
                lines.append("Unread inbox email:")
                for m in mail["unread"]:
                    lines.append(f"- {m['from'][:40]}: {m['subject'][:80]}")
    except Exception:
        pass

    last = state.facts().get(AWARENESS_LAST_MSG) or ""
    if last:
        lines.append(f"(You recently told him: {last[:150]})")
    return "\n".join(lines)


def awareness_pulse() -> bool:
    """One self-check. Returns whether it decided to reach the Boss."""
    snap = _world_snapshot()
    # Nothing in his world but the clock -> nothing to reason about, no spend.
    if snap.count("\n") < 1:
        return False

    import json as _json

    import models
    from openai import OpenAI
    try:
        r = OpenAI(timeout=30.0, max_retries=1).responses.create(
            model=models.BRAIN, instructions=_AWARENESS_PROMPT,
            input=[{"role": "user", "content": snap}],
            reasoning={"effort": "low"}, max_output_tokens=400, store=False)
        models.record(r, models.BRAIN)
        raw = (r.output_text or "").strip()
    except Exception as e:
        log(f"awareness model failed: {type(e).__name__}: {e}")
        return False

    try:
        a, b = raw.find("{"), raw.rfind("}")
        obj = _json.loads(raw[a:b + 1]) if (a >= 0 and b > a) else {}
    except Exception:
        return False
    msg = str(obj.get("message", "")).strip()
    if not (obj.get("act") and msg):
        return False

    state.set_fact(AWARENESS_LAST_MSG, msg[:200])
    if obj.get("urgent"):
        call_boss(msg)                      # call -> Telegram fallback
    else:
        tell_boss(msg)                      # quiet msg, through the cooldown gate
    log(f"awareness -> Boss: {msg[:90]}")
    return True


def maybe_awareness() -> bool:
    """Run the awareness pulse on its interval. Cheap when idle."""
    try:
        last = float(state.facts().get(AWARENESS_KEY) or 0)
    except (TypeError, ValueError):
        last = 0.0
    if time.time() - last < AWARENESS_EVERY_S:
        return False
    state.set_fact(AWARENESS_KEY, f"{time.time():.0f}")
    try:
        return awareness_pulse()
    except Exception as e:
        log(f"awareness failed: {type(e).__name__}: {e}")
        return False


HEARTBEAT = ROOT / "data" / "worker.heartbeat"


def _beat() -> None:
    """Prove the worker is alive, so main.py's watchdog can restart it if it
    dies - otherwise the awareness loop and every job silently stop."""
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    log(f"worker up. polling every {TICK_S:.0f}s. db={state.DB_PATH}")
    while True:
        try:
            _beat()
            tick()
            maybe_brief()
            maybe_awareness()
        except KeyboardInterrupt:
            log("worker stopped.")
            return 0
        except Exception:
            log(f"tick failed: {traceback.format_exc()[-300:]}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    raise SystemExit(main())
