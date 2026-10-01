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

TICK_S = 10.0                 # how often the queue is read
MAX_JOB_AGE_S = 6 * 3600      # a watch that has run this long has been forgotten
LOG = ROOT / "worker.log"


def log(line: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    msg = f"{stamp} {line}"
    print(msg, flush=True)
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

        reply = _compose_reply(jid, contact, text, persona)
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


def _compose_reply(jid: str, contact: str, incoming: str, persona: str) -> str:
    """One model call, with this contact's state loaded first.

    IDENTITY IS ANCHORED HERE. The old build replied "I'm ChatGPT, helping my
    friend with the conversation" - because there was no reply path at all and
    no prompt that said who it was. It was the raw model free-typing into a GUI."""
    import models
    from openai import OpenAI

    identity = _persona_text(persona)
    ctx = state.as_context(jid)
    fenced = safety.fence(incoming, f"a WhatsApp message from {contact}")

    # THE CONVERSATION SO FAR, not just the latest line.
    #
    # This used to pass only the incoming message and a summary, so every
    # reply answered one sentence in isolation - which is exactly what "keep
    # talking to him until he says bye" is not. A person who has just been
    # asked "and what time?" needs to know what was being arranged.
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
            instructions=identity + ctx,
            input=[{"role": "user", "content":
                    f"{thread}\n\n{fenced}\n\nReply as yourself, in one or two "
                    f"short sentences, the way a person texts. Continue the "
                    f"conversation above - do not start over or re-introduce "
                    f"yourself."}],
            reasoning={"effort": "low"},
            max_output_tokens=400, store=False)
        models.record(r, models.BRAIN)
        reply = (r.output_text or "").strip()
    except Exception as e:
        log(f"reply generation failed: {type(e).__name__}: {e}")
        return ""

    if reply:
        state.add_message(jid, "out", reply)
        state.update_thread(jid, summary=(state.thread(jid)["summary"] or "")[:400])
    return reply[:900]


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


KINDS = {"watch_chat": watch_chat, "reminder": reminder,
         "poll_url": poll_url, "check_process": check_process}

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


def main() -> int:
    log(f"worker up. polling every {TICK_S:.0f}s. db={state.DB_PATH}")
    while True:
        try:
            tick()
            maybe_brief()
        except KeyboardInterrupt:
            log("worker stopped.")
            return 0
        except Exception:
            log(f"tick failed: {traceback.format_exc()[-300:]}")
        time.sleep(TICK_S)


if __name__ == "__main__":
    raise SystemExit(main())
