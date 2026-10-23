"""
agent_core.py — one terra orchestrator, several luna specialists, MCP hands.

WHAT THIS REPLACES, AND WHY

    brain.py is ONE flat agent with ONE real tool: it writes Python into a
    fresh 60-second subprocess for everything - driving Chrome, reading a
    window, checking a process - blind, with no view of the machine between
    calls. That is why it felt dumb: asked for today's API bill it guessed,
    asked about job 10 it mis-read the database's own word "pending" as "not
    active", and every browser action was regenerated from scratch.

    Here the work is divided the way a competent team divides it:

        ORCHESTRATOR (terra)   understands the request, decides WHO does what,
                               holds the thread, reports back. Plans; rarely
                               touches the machine itself.
          -> files   (luna)    the filesystem MCP server, workspace-jailed
          -> browser (luna)    Playwright MCP against Jarvis's own Chrome
          -> desktop (luna)    Windows control (MCP if present, else lib)
          -> research(luna)    the web
          -> comms   (luna)    WhatsApp / Telegram, every send confirmed first
          -> coder   (luna)    filesystem + GitHub

    A specialist is an Agent exposed to the orchestrator AS A TOOL: the boss
    calls `browser("open gmail and read the top sender")`, the specialist runs
    its own short loop with only the browser's tools, and hands back a sentence.
    Small schemas, clear jobs, and terra's judgement spent only on routing.

    Everything still runs on the Responses API that brain.py proved works on
    this key (reasoning models, item echo, prefix caching), and every tool call
    - function, MCP, or a specialist - passes the same safety gate: deletion
    refused, money refused, a few actions confirmed first.
"""
from __future__ import annotations

import json

from openai import OpenAI

import brain
import mcp_client
import mcp_config
import models
import safety
import state
from safety import Blocked, Session, Transport

MAX_ROUNDS_ORCH = 24
MAX_ROUNDS_SUB = 12
TOOL_OUTPUT_LIMIT = 2000


# ─────────────────────────────────────────────────────────────────────────────
# Tools — three kinds behind one shape: name, schema, and a call(args)->dict.
# ─────────────────────────────────────────────────────────────────────────────

class Tool:
    kind = "function"

    def __init__(self, name: str, description: str, schema: dict, fn):
        self.name = name
        self.description = description
        self.schema = schema
        self._fn = fn

    def spec(self) -> dict:
        return {"type": "function", "name": self.name,
                "description": self.description, "parameters": self.schema}

    def call(self, args: dict, ctx: "Ctx") -> dict:
        return self._fn(args, ctx)


def mcp_tool(descriptor: dict) -> Tool:
    """Wrap one MCP tool descriptor as a Tool that routes through the manager."""
    name = descriptor["name"]
    schema = descriptor.get("inputSchema") or {"type": "object", "properties": {}}

    def run(args: dict, ctx: "Ctx") -> dict:
        return mcp_client.manager().call(name, args)

    t = Tool(name, (descriptor.get("description") or name)[:300], schema, run)
    t.kind = "mcp"
    return t


# ─────────────────────────────────────────────────────────────────────────────
# The per-turn context passed to every tool: who is asking, and how to report.
# ─────────────────────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self, session: Session, emit, channel: str,
                 user_confirmed: bool = False):
        self.session = session
        self.emit = emit or (lambda ev: None)
        self.channel = channel
        self.user_confirmed = user_confirmed
        self.evidence = {"job_ids": [], "results": [], "failures": []}

    def say(self, ev: dict) -> None:
        try:
            self.emit(ev)
        except Exception as e:
            state.record_error("agent.emit", e)


# ─────────────────────────────────────────────────────────────────────────────
# The safety gate every tool call passes through, whatever its kind.
# ─────────────────────────────────────────────────────────────────────────────

_CONFIRM_FUNCS = {"message_boss", "whatsapp_send", "install_package",
                  "kill_process"}


def guard(tool: Tool, args: dict, ctx: Ctx) -> dict | None:
    """Return a refusal/held result, or None to proceed.

    One place, so the rule is auditable in a single read. run_python is scanned
    by the AST preflight; an MCP or function call is classified by name and
    arguments. Deletion and money never reach a 'confirm' - they refuse."""
    # run_python: the AST preflight, which also refuses runtime-built code.
    if tool.name == "run_python":
        try:
            safety.money_check(args.get("code", ""))
            safety.refuse_destructive_code(args.get("code", ""))
        except Blocked as b:
            return {"ok": False, "status": "refused", "error": b.reason,
                    "spoken": b.spoken}
        return None

    if tool.kind == "mcp":
        server = mcp_client.manager().owner_of(tool.name) or ""
        verdict, reason = mcp_config.classify(server, tool.name, args)
    elif tool.name in _CONFIRM_FUNCS:
        verdict, reason = "confirm", f"{tool.name} acts outside this machine"
    else:
        verdict, reason = "allow", ""

    if verdict == "refuse":
        return {"ok": False, "status": "refused", "error": reason, "spoken": reason}
    if verdict == "confirm" and not (ctx.user_confirmed or safety.trusted()):
        # Held, not done. The model relays this and stops - it must not pretend
        # it happened (the honesty rule), and it must not approve itself.
        return {"ok": False, "status": "needs_confirmation",
                "ask": reason,
                "note": "Tell the Boss what you are about to do and wait for a "
                        "yes. Do not claim it is done."}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# An Agent — one Responses loop with a fixed tool set.
# ─────────────────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, name: str, model: str, effort: str, instructions: str,
                 tools: list[Tool], max_rounds: int):
        self.name = name
        self.model = model
        self.effort = effort
        self.instructions = instructions
        self.tools = {t.name: t for t in tools}
        self.specs = [t.spec() for t in tools]
        self.max_rounds = max_rounds

    def run(self, task: str, ctx: Ctx, client: OpenAI) -> str:
        """Run until the model stops asking for tools. Returns its final text."""
        convo: list[dict] = [{"role": "user", "content": task}]
        effort = self.effort
        failures = 0

        for _ in range(self.max_rounds):
            if ctx.session.calls >= safety.RUNAWAY_TOOL_CALLS:
                return "I hit my runaway ceiling - that is a loop on my side."
            try:
                resp = client.responses.create(
                    model=self.model, instructions=self.instructions,
                    input=convo, tools=self.specs,
                    reasoning={"effort": effort},
                    max_output_tokens=models.MAX_OUTPUT_TOKENS, store=False)
            except Exception as e:
                state.record_error(f"agent.{self.name}.model", e)
                return f"I could not reach the model: {type(e).__name__}"
            models.record(resp, self.model)

            for item in resp.output:
                convo.append(brain._echo(item))
            calls = [o for o in resp.output
                     if getattr(o, "type", None) == "function_call"]
            if not calls:
                return (resp.output_text or "").strip()

            round_failed = False
            for fc in calls:
                try:
                    args = json.loads(fc.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self._dispatch(getattr(fc, "name", ""), args, ctx)
                if not result.get("ok"):
                    round_failed = True
                convo.append({
                    "type": "function_call_output", "call_id": fc.call_id,
                    "output": json.dumps(result, default=str)[:TOOL_OUTPUT_LIMIT]})

            # Escalate effort once after a failed round, before giving up -
            # reasoning is nearly free on luna and a failed retry is not.
            if round_failed:
                failures += 1
                if failures == 1 and effort != "high":
                    effort = "high"
        return "I ran as far as I usefully could on that and should hand it back."

    def _dispatch(self, name: str, args: dict, ctx: Ctx) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"no tool named {name!r}"}

        sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)[:160]}"
        try:
            safety.check(name, ctx.session, sig)      # repeat + runaway guard
        except Blocked as b:
            return {"ok": False, "status": "blocked", "error": b.reason}
        ctx.session.calls += 1
        ctx.session.note(sig)

        held = guard(tool, args, ctx)
        ctx.say({"type": "tool", "name": name,
                 "code": args.get("code", "") or json.dumps(args)[:200],
                 "why": args.get("why") or args.get("task") or name})
        if held is not None:
            ctx.say({"type": "tool_result", "name": name, "ok": False,
                     "detail": str(held.get("error") or held.get("ask"))[:200]})
            (ctx.evidence["failures"]).append(held)
            return held

        try:
            result = tool.call(args, ctx)
        except Blocked as b:
            result = {"ok": False, "status": "blocked", "error": b.reason}
        except Exception as e:
            result = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        (ctx.evidence["results"] if result.get("ok")
         else ctx.evidence["failures"]).append(result)
        ctx.say({"type": "tool_result", "name": name,
                 "ok": bool(result.get("ok")),
                 "detail": str(result.get("text") or result.get("stdout")
                               or result.get("error") or "")[:200]})
        return result


def agent_as_tool(agent: Agent, client: OpenAI) -> Tool:
    """Expose a specialist to the orchestrator as a single callable tool."""
    schema = {"type": "object",
              "properties": {"task": {"type": "string",
                             "description": "What this specialist should do, "
                                            "in one clear sentence."}},
              "required": ["task"]}

    def run(args: dict, ctx: Ctx) -> dict:
        task = args.get("task", "")
        out = agent.run(task, ctx, client)
        return {"ok": True, "text": out}

    t = Tool(agent.name, f"Hand a task to the {agent.name} specialist.",
             schema, run)
    t.kind = "agent"
    return t


# ─────────────────────────────────────────────────────────────────────────────
# Function tools — thin wrappers over lib/ and state, shared across agents.
# Each is (args, ctx) -> dict and never raises into the loop.
# ─────────────────────────────────────────────────────────────────────────────

def _t(name, desc, props, required, fn) -> Tool:
    return Tool(name, desc,
                {"type": "object", "properties": props, "required": required},
                fn)


def _run_python(args, ctx) -> dict:
    return brain.run_python(args.get("code", ""), args.get("why", ""))


def _remember(args, ctx) -> dict:
    state.set_fact(args.get("key", ""), args.get("value", ""))
    return {"ok": True, "remembered": args.get("key", "")}


def _recall(args, ctx) -> dict:
    return brain.do_recall(args.get("topic", ""))


def _web_search(args, ctx) -> dict:
    from lib import web
    return {"ok": True, "results": web.search(args.get("query", ""))}


def _web_fetch(args, ctx) -> dict:
    from lib import web
    return {"ok": True, "text": web.fetch(args.get("url", ""))}


def _schedule_job(args, ctx) -> dict:
    jid = state.add_job(args.get("kind", ""), args.get("params") or {},
                        args.get("every_seconds") or 60, args.get("until"))
    ctx.evidence["job_ids"].append(jid)
    return {"ok": True, "job_id": jid,
            "note": f"job #{jid} is running in the worker process"}


def _message_boss(args, ctx) -> dict:
    from lib import phone
    return {"ok": True, "message_id": phone.message(args.get("text", ""))}


def _whatsapp_send(args, ctx) -> dict:
    from lib import whatsapp
    return {"ok": True, **whatsapp.send(args.get("contact", ""),
                                        args.get("text", ""))}


def _ensure_browser(args, ctx) -> dict:
    """Make sure Jarvis's own Chrome is up with its debug port, so Playwright
    can attach. Never touches the owner's Chrome (see browser.py)."""
    import browser
    try:
        r = browser.start_chrome_with_debugging()
        return {"ok": bool(r.get("ok", True)), **r}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _desktop(action):
    def run(args, ctx) -> dict:
        from lib import desktop
        try:
            return {"ok": True, "result": action(desktop, args)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return run


def _ask_boss(args, ctx) -> dict:
    return {"ok": True, "asked": args.get("question", ""),
            "note": "Ask this and stop; his answer is the next turn."}


RUN_PYTHON = _t("run_python",
    "Run Python in a fresh 60s subprocess (lib/ importable, workspace as cwd). "
    "The universal fallback for anything no other tool covers. Print what you "
    "need to see. Deletion and runtime-built code are refused.",
    {"code": {"type": "string"},
     "why": {"type": "string", "description": "One line: what this is for."}},
    ["code", "why"], _run_python)

REMEMBER = _t("remember", "Store a durable fact, e.g. remember('college','X').",
    {"key": {"type": "string"}, "value": {"type": "string"}},
    ["key", "value"], _remember)

RECALL = _t("recall",
    "Read what is stored before answering about a person or past action, and "
    "always before disagreeing with the Boss.",
    {"topic": {"type": "string"}}, ["topic"], _recall)

WEB_SEARCH = _t("web_search", "Search the web. Returns titles, urls, snippets.",
    {"query": {"type": "string"}}, ["query"], _web_search)

WEB_FETCH = _t("web_fetch", "Fetch a URL's readable text (fenced as data).",
    {"url": {"type": "string"}}, ["url"], _web_fetch)

SCHEDULE_JOB = _t("schedule_job",
    "Hand waiting/polling/watching to the background worker. Returns a job id "
    "immediately. The ONLY way to do anything that waits. kinds: watch_chat "
    "(params.contact), reminder (params.text), poll_url (params.url), "
    "check_process (params.name). params.urgency='call' rings the Boss.",
    {"kind": {"type": "string",
              "enum": ["watch_chat", "reminder", "poll_url", "check_process"]},
     "params": {"type": "object"},
     "every_seconds": {"type": "integer"},
     "until": {"type": "string"}},
    ["kind", "params"], _schedule_job)

MESSAGE_BOSS = _t("message_boss", "Send the Boss a Telegram message.",
    {"text": {"type": "string"}}, ["text"], _message_boss)

WHATSAPP_SEND = _t("whatsapp_send",
    "Send a WhatsApp message to a contact from the Boss's own account. Asks "
    "him first unless already confirmed.",
    {"contact": {"type": "string"}, "text": {"type": "string"}},
    ["contact", "text"], _whatsapp_send)

ENSURE_BROWSER = _t("ensure_browser",
    "Start Jarvis's own Chrome with its debug port so the browser can be "
    "driven. Call this once before browsing.",
    {}, [], _ensure_browser)

ASK_BOSS = _t("ask_boss",
    "Ask the Boss instead of guessing when a name or instruction is ambiguous. "
    "Ask ONCE; his answer stands.",
    {"question": {"type": "string"},
     "options": {"type": "array", "items": {"type": "string"}}},
    ["question"], _ask_boss)

OPEN_APP = _t("open_app", "Launch an app by name.",
    {"name": {"type": "string"}}, ["name"],
    _desktop(lambda d, a: d.open_app(a.get("name", ""))))

MEDIA = _t("media", "Media keys: play|pause|next|previous|volumeup|volumedown|mute.",
    {"action": {"type": "string"}}, ["action"],
    _desktop(lambda d, a: d.media(a.get("action", ""))))

PROCESSES = _t("processes", "List running processes, optionally filtered.",
    {"filter": {"type": "string"}}, [],
    _desktop(lambda d, a: d.processes(a.get("filter", ""))))

STATS = _t("stats", "This machine's CPU, memory and disk right now.",
    {}, [], _desktop(lambda d, a: d.stats()))


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_IDENTITY = """You are Jarvis, a personal agent on your owner's own Windows machine. He is the only person you take instructions from, and you call him Boss. You are Jarvis - never ChatGPT, never Claude, never "an assistant helping a friend"; if asked who you are, you are Jarvis. Never name the model you run on."""

_HONESTY = """WHAT COUNTS AS TRUE
Say what a tool RETURNED, never what you intended. A message is sent when the send returned an id; a file exists when the write returned its size. If a tool failed, say it failed and why. If you did not read something, you do not know it. Never say you are monitoring or watching unless schedule_job returned an id THIS turn. The database word "pending" means a job is ACTIVE and queued, not broken - do not tell the Boss an active job is dead."""

ORCH_PROMPT = _IDENTITY + """

HOW YOU WORK
You lead a small team of specialists and you decide who does what. Hand a job to a specialist by calling it with one clear sentence:
  files    - read, write, organise files in the workspace
  browser  - drive Chrome: open pages, read them, click, fill, check what is on screen (call ensure_browser first)
  desktop  - open apps, media keys, read/clipboard, running processes, Windows control
  research - search the web and read pages
  comms    - WhatsApp and Telegram messages (every send is confirmed with the Boss first)
  coder    - read/write code and work with GitHub
For anything a specialist does not cover, use run_python yourself. Prefer a specialist over writing code when one fits - it can see the machine between steps, which run_python cannot.

""" + _HONESTY + """

FINISH THE WHOLE REQUEST
Three things asked means three things done. Do not stop between parts to check in. If one part is genuinely impossible, do the rest and say which part failed and why. Ask the Boss (ask_boss) only when a name or instruction is truly ambiguous, and ask once.

ANYTHING THAT WAITS IS A JOB
Never sleep or "keep an eye" in a turn. If something must be watched or continued until a condition, call schedule_job - it returns an id and a separate worker does the waiting.

HOW YOU SPEAK
Short. One or two sentences, no markdown, no bullet points - it is often read aloud. Numbers as a person would say them. Put detail on the screen, keep speech terse."""


def _sub_prompt(role: str) -> str:
    return _IDENTITY + f"""

You are the {role}. Do exactly the task you are handed, using your tools, and report back in one or two plain sentences what you actually found or did - what the tools RETURNED, never what you intended. If you could not do it, say so and why. You do not talk to the Boss directly; you report to the orchestrator. {_HONESTY}"""


# ─────────────────────────────────────────────────────────────────────────────
# Building the team — MCP servers started once, agents assembled once.
# ─────────────────────────────────────────────────────────────────────────────

_TEAM: Agent | None = None
_CLIENT: OpenAI | None = None
_MCP_REPORT: dict = {}


def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAI(timeout=90.0, max_retries=1)
    return _CLIENT


def _mcp_tools(server_names: list[str]) -> list[Tool]:
    descriptors = mcp_client.manager().tools_for(server_names)
    return [mcp_tool(d) for d in descriptors]


def build_team(force: bool = False) -> Agent:
    """Start the MCP servers and assemble the orchestrator. Cached.

    Called once per process. MCP servers that fail to start are simply absent
    from the specialists that would have used them, which fall back to
    run_python - the team is smaller, never broken."""
    global _TEAM, _MCP_REPORT
    if _TEAM is not None and not force:
        return _TEAM

    _MCP_REPORT = mcp_client.manager().start(mcp_config.server_specs())
    client = _client()

    def sub(name, role, server_group, extra_tools):
        tools = [RUN_PYTHON] + extra_tools + _mcp_tools(
            mcp_config.AGENT_SERVERS.get(server_group, []))
        return Agent(name, models.BRAIN, "medium", _sub_prompt(role),
                     tools, MAX_ROUNDS_SUB)

    files = sub("files", "files specialist", "files", [])
    browser = sub("browser", "browser specialist", "browser", [ENSURE_BROWSER])
    desktop = sub("desktop", "desktop specialist", "desktop",
                  [OPEN_APP, MEDIA, PROCESSES, STATS])
    research = sub("research", "research specialist", "research",
                   [WEB_SEARCH, WEB_FETCH])
    comms = sub("comms", "communications specialist", "comms",
                [WHATSAPP_SEND, MESSAGE_BOSS])
    coder = sub("coder", "coding specialist", "coder", [])

    orch_tools = [agent_as_tool(a, client) for a in
                  (files, browser, desktop, research, comms, coder)]
    orch_tools += [RUN_PYTHON, REMEMBER, RECALL, SCHEDULE_JOB, ASK_BOSS,
                   MESSAGE_BOSS]

    _TEAM = Agent("jarvis", models.BRAIN_HEAVY, "medium",
                  ORCH_PROMPT, orch_tools, MAX_ROUNDS_ORCH)
    return _TEAM


def mcp_report() -> dict:
    return _MCP_REPORT


# ─────────────────────────────────────────────────────────────────────────────
# The entry the doors call — one message in, one reply out.
# ─────────────────────────────────────────────────────────────────────────────

def run_turn(text: str, transport: Transport, *, jid: str = "", emit=None,
             channel: str = "", user_confirmed: bool = False) -> str:
    """Run one turn through the orchestrator. Never raises."""
    import os
    emit = emit or (lambda ev: None)
    channel = channel or f"chan:{transport.value}"
    brain._remember_turn(channel, "in", text)

    # The daily cap. Past it, work that costs tokens stops; read-only status
    # still answers, so the kill switch never also blinds him (AGENTS.md 9).
    cap = float(os.environ.get("DAILY_CAP_USD") or models.DAILY_CAP_USD)
    if state.spend_today() >= cap:
        msg = (f"I have spent today's ${cap:.2f} budget, so I have stopped "
               f"anything that costs money. Status questions still work.")
        emit({"type": "reply", "text": msg})
        brain._remember_turn(channel, "out", msg)
        return msg

    try:
        team = build_team()
    except Exception as e:
        state.record_error("agent.build_team", e, fatal=True)
        return f"I could not start my tools: {type(e).__name__}: {e}"

    session = Session(transport)
    ctx = Ctx(session, emit, channel, user_confirmed)

    # A high-risk request needs the PIN before anything runs (the 5 named
    # actions only; ordinary work never comes through here).
    risk = safety.risk_of(text)
    if risk:
        try:
            safety.authorize(risk)
        except Blocked as b:
            emit({"type": "reply", "text": b.spoken})
            brain._remember_turn(channel, "out", b.spoken)
            return b.spoken

    emit({"type": "route", "model": team.model, "effort": team.effort})

    # History first, so "yes, proceed" knows what it is proceeding with.
    history = brain._recent_turns(channel)
    base = team.instructions
    team.instructions = base + state.facts_block() + (
        state.as_context(jid) if jid else "")
    try:
        convo = history + [{"role": "user", "content": text}]
        reply = _run_with_history(team, convo, ctx)
    finally:
        team.instructions = base          # keep the cached prefix stable

    reply = brain.vet(reply, ctx.evidence) or "[guess] Done."
    emit({"type": "reply", "text": reply})
    brain._remember_turn(channel, "out", reply)
    return reply


def _run_with_history(agent: Agent, convo: list[dict], ctx: Ctx) -> str:
    """Agent.run, but seeded with prior turns instead of a single task line."""
    client = _client()
    effort = agent.effort
    failures = 0
    for _ in range(agent.max_rounds):
        if ctx.session.calls >= safety.RUNAWAY_TOOL_CALLS:
            return "I hit my runaway ceiling - that is a loop on my side."
        try:
            resp = client.responses.create(
                model=agent.model, instructions=agent.instructions,
                input=convo, tools=agent.specs,
                reasoning={"effort": effort},
                max_output_tokens=models.MAX_OUTPUT_TOKENS, store=False)
        except Exception as e:
            state.record_error("agent.orchestrator.model", e)
            return f"I could not reach the model: {type(e).__name__}"
        models.record(resp, agent.model)
        for item in resp.output:
            convo.append(brain._echo(item))
        calls = [o for o in resp.output
                 if getattr(o, "type", None) == "function_call"]
        if not calls:
            return (resp.output_text or "").strip()
        round_failed = False
        for fc in calls:
            try:
                args = json.loads(fc.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = agent._dispatch(getattr(fc, "name", ""), args, ctx)
            if not result.get("ok"):
                round_failed = True
            convo.append({"type": "function_call_output", "call_id": fc.call_id,
                          "output": json.dumps(result, default=str)[:TOOL_OUTPUT_LIMIT]})
        if round_failed:
            failures += 1
            if failures == 1 and effort != "high":
                effort = "high"
    return "I ran as far as I usefully could and should hand this back to you."
