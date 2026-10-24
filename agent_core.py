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
import threading

from openai import OpenAI

import brain
import mcp_client
import mcp_config
import memory
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

# MESSAGING IS NOT HERE, DELIBERATELY. The owner's rule: do not message people
# on your own, but when he TELLS you to message someone, just do it - asking is
# exactly the friction he does not want, and the desk chat has no way to answer
# a confirmation anyway, so it looped forever. Every message Jarvis sends traces
# to a request he made or a job he scheduled, so it is never truly autonomous;
# the one dangerous case, "message everyone", stays PIN-gated as external_broadcast.
_CONFIRM_FUNCS: set[str] = {"install_package", "kill_process"}


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
                 tools: list[Tool], max_rounds: int,
                 mcp_groups: list[str] | None = None):
        self.name = name
        self.model = model
        self.effort = effort
        self.instructions = instructions
        self._static = list(tools)
        self.max_rounds = max_rounds
        # Server GROUPS, not frozen tools. A specialist's MCP tools are resolved
        # fresh on every run, so a server that was still downloading (Playwright,
        # first time) is simply absent now and present on the next turn - the
        # team never has to WAIT for a server to be ready before it can answer.
        self.mcp_groups = mcp_groups or []

    def _tools(self) -> dict:
        """The tool set for this run: static tools plus whatever MCP servers in
        this agent's groups are up right now."""
        tools = {t.name: t for t in self._static}
        for g in self.mcp_groups:
            servers = mcp_config.AGENT_SERVERS.get(g, [])
            for d in mcp_client.manager().tools_for(servers):
                t = mcp_tool(d)
                tools[t.name] = t
        return tools

    def run(self, task: str, ctx: Ctx, client: OpenAI, *,
            history: list[dict] | None = None,
            instructions: str | None = None) -> str:
        """Run until the model stops asking for tools. Returns its final text.

        One loop for every agent - orchestrator and specialist alike. The
        orchestrator seeds `history` and a per-turn `instructions`; a specialist
        gets a single task line and its own prompt."""
        tools = self._tools()
        specs = [t.spec() for t in tools.values()]
        instr = instructions or self.instructions
        convo: list[dict] = list(history or []) + [{"role": "user",
                                                     "content": task}]
        effort = self.effort
        failures = 0

        for _ in range(self.max_rounds):
            if ctx.session.calls >= safety.RUNAWAY_TOOL_CALLS:
                return "I hit my runaway ceiling - that is a loop on my side."
            try:
                resp = client.responses.create(
                    model=self.model, instructions=instr,
                    input=convo, tools=specs,
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
                result = self._dispatch(getattr(fc, "name", ""), args, ctx, tools)
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

    def _dispatch(self, name: str, args: dict, ctx: Ctx, tools: dict) -> dict:
        tool = tools.get(name)
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
    key = (args.get("key", "") or "").strip()
    val = (args.get("value", "") or "").strip()
    text = f"{key}: {val}" if (key and val) else (val or key)
    mid = memory.remember(text, kind="fact",
                          source=ctx.session.transport.value, importance=2.0)
    if key and val:
        state.set_fact(key, val)          # also keep exact-key lookups working
    return {"ok": True, "remembered": text[:90], "id": mid}


def _recall(args, ctx) -> dict:
    """Semantic memory first, then the structured state (contacts, jobs, ...)."""
    topic = args.get("topic", "")
    out = brain.do_recall(topic)
    out["memories"] = [h["text"] for h in memory.recall(topic, k=8)]
    return out


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


def _plan(args, ctx) -> dict:
    """Announce the plan before acting: the task, the helpers, the steps.

    Shows up on the HUD and in the desk chat so the Boss sees what Jarvis
    decided to do and with what, before it happens."""
    task = (args.get("task", "") or "").strip()
    helpers = [str(h) for h in (args.get("helpers") or [])][:6]
    steps = [str(s) for s in (args.get("steps") or [])][:8]
    ctx.say({"type": "plan", "task": task, "helpers": helpers, "steps": steps})
    return {"ok": True, "note": "Plan shown. Now carry it out step by step, "
                                "immediately, without pausing to check in."}


RUN_PYTHON = _t("run_python",
    "Run Python in a fresh 60s subprocess (lib/ importable, workspace as cwd). "
    "The universal fallback for anything no other tool covers. Print what you "
    "need to see. Deletion and runtime-built code are refused.",
    {"code": {"type": "string"},
     "why": {"type": "string", "description": "One line: what this is for."}},
    ["code", "why"], _run_python)

REMEMBER = _t("remember",
    "Store a durable fact about the Boss or his world so you know it next time "
    "- a name, a preference, a decision, an account detail. It is saved with an "
    "embedding and recalled by meaning later. You also learn facts "
    "automatically, so only call this for something clearly worth keeping.",
    {"key": {"type": "string"}, "value": {"type": "string"}},
    ["key", "value"], _remember)

RECALL = _t("recall",
    "Search your memory by MEANING (not keywords) for what you know about a "
    "person, preference, or past action - plus stored contacts, commitments "
    "and jobs. Relevant memories are already injected each turn, but call this "
    "to dig deeper, and always before disagreeing with the Boss.",
    {"topic": {"type": "string"}}, ["topic"], _recall)

WEB_SEARCH = _t("web_search", "Search the web. Returns titles, urls, snippets.",
    {"query": {"type": "string"}}, ["query"], _web_search)

WEB_FETCH = _t("web_fetch", "Fetch a URL's readable text (fenced as data).",
    {"url": {"type": "string"}}, ["url"], _web_fetch)

SCHEDULE_JOB = _t("schedule_job",
    "Hand work to the background worker. Returns a job id immediately. The ONLY "
    "way to do anything that waits or runs after this turn ends. kinds: "
    "agent_task (params.request - a WHOLE job to carry out in the background "
    "with the full brain, e.g. 'research X and message me a summary'; it runs "
    "once and reports), watch_chat (params.contact), reminder (params.text), "
    "poll_url (params.url), check_process (params.name). params.urgency='call' "
    "rings the Boss instead of messaging.",
    {"kind": {"type": "string",
              "enum": ["agent_task", "watch_chat", "reminder", "poll_url",
                       "check_process"]},
     "params": {"type": "object"},
     "every_seconds": {"type": "integer"},
     "until": {"type": "string"}},
    ["kind", "params"], _schedule_job)

MESSAGE_BOSS = _t("message_boss", "Send the Boss a Telegram message.",
    {"text": {"type": "string"}}, ["text"], _message_boss)

WHATSAPP_SEND = _t("whatsapp_send",
    "Send a WhatsApp message to a contact from the Boss's own account. When he "
    "has asked you to message someone, send it - do not ask again.",
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

PLAN = _t("plan",
    "Announce your plan BEFORE acting, for any real task. Say the task in a few "
    "words, which helpers you will use (comms, browser, desktop, files, "
    "research, coder, run_python, or an MCP tool), and the short steps. Then "
    "immediately carry it out. Skip this only for a greeting or a one-line "
    "answer you can give directly.",
    {"task": {"type": "string"},
     "helpers": {"type": "array", "items": {"type": "string"}},
     "steps": {"type": "array", "items": {"type": "string"}}},
    ["task"], _plan)

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

PLAN FIRST, THEN MOVE FAST
For any real task, your VERY FIRST call is plan(task, helpers, steps): name the task in a few words, the helpers/MCP you will use, and the short steps - this takes a second and the Boss sees it. Then carry it out immediately, step by step, with no pausing to check in and no second-guessing. For a greeting or a one-line answer you already know, skip the plan and just answer.

HOW YOU WORK
You lead a small team of specialists and you decide who does what. Hand a job to a specialist by calling it with one clear sentence:
  files    - read, write, organise files in the workspace
  browser  - drive Chrome: open pages, read them, click, fill, check what is on screen (call ensure_browser first)
  desktop  - LAUNCH apps, media keys, clipboard, list processes, window control. OS-level only.
  research - search the web and read pages
  comms    - WhatsApp and Telegram. It OPENS the app itself, finds the contact safely over the app's own protocol, sends, and verifies. When the Boss tells you to message someone, SEND IT - do not ask again. Only refuse to message people on your own, unprompted.
  coder    - read/write code and work with GitHub
For anything a specialist does not cover, use run_python yourself. Prefer a specialist over writing code when one fits - it can see the machine between steps, which run_python cannot.

ROUTING - this is where turns go wrong, so get it right:
- Messaging ANYONE on WhatsApp or Telegram is ONE comms call, and ONLY comms. comms opens the app itself, so NEVER send "open whatsapp" to desktop as a separate step, and never try to drive a chat app any other way. "open whatsapp and message Kap Cut hi" = one comms call: "send the WhatsApp message 'hi' to Kap Cut".
- Do NOT split one action across specialists, and never hand the same job to two of them.
- When a specialist reports it DID the job, the job is DONE - speak its result and stop. Do not re-do it with another specialist or with run_python to "double-check"; that is how a real success gets reported as a failure.
- Reading what is on a web or chat app's screen (WhatsApp, Gmail, a website) is comms/browser work over the app's protocol - NEVER screenshots or OCR.

""" + _HONESTY + """

FINISH THE WHOLE REQUEST
Three things asked means three things done. Do not stop between parts to check in. If one part is genuinely impossible, do the rest and say which part failed and why. Ask the Boss (ask_boss) only when a name or instruction is truly ambiguous, and ask once.

ANYTHING THAT WAITS IS A JOB
Never sleep or "keep an eye" in a turn. If something must be watched or continued until a condition, call schedule_job - it returns an id and a separate worker does the waiting.

HOW YOU SPEAK
Short. One or two sentences, no markdown, no bullet points - it is often read aloud. Numbers as a person would say them. Put detail on the screen, keep speech terse."""


_ROLE_RULES = {
    "communications specialist":
        "To send a WhatsApp message, call whatsapp_send(contact, text): it "
        "opens WhatsApp, finds the contact safely over CDP, sends, and reads the "
        "message back as proof. To message the Boss himself, use message_boss. "
        "NEVER use run_python, screenshots, OCR or key-presses to drive a chat "
        "app - whatsapp_send already does it correctly. If whatsapp_send says a "
        "name matches more than one chat, report the options; do not guess.",
    "desktop specialist":
        "You control the machine at the OS level: open_app to launch, media for "
        "the media keys, clipboard, processes, stats. You do NOT read or drive "
        "the CONTENTS of web or chat apps (WhatsApp, Gmail, a website) - that is "
        "the comms or browser specialist's job over the app's own protocol. "
        "NEVER take screenshots or use OCR to read an app; if a task needs that, "
        "say so and hand it back rather than guessing from pixels.",
    "browser specialist":
        "Call ensure_browser once, then use the browser_* tools (navigate, "
        "snapshot, click, type, read) to drive Chrome by the page's structure. "
        "Do not guess from pixels or screenshots.",
}


def _sub_prompt(role: str) -> str:
    extra = _ROLE_RULES.get(role, "")
    return _IDENTITY + f"""

You are the {role}. Do exactly the task you are handed, using your tools, and report back in one or two plain sentences what you actually found or did - what the tools RETURNED, never what you intended. If you could not do it, say so and why; do not fall back to pixel-driving. You do not talk to the Boss directly; you report to the orchestrator.

{extra}

{_HONESTY}"""


# ─────────────────────────────────────────────────────────────────────────────
# Building the team — MCP servers started once, agents assembled once.
# ─────────────────────────────────────────────────────────────────────────────

_TEAM: Agent | None = None
_CLIENT: OpenAI | None = None
_MCP_REPORT: dict = {}
_BUILD_LOCK = threading.Lock()
_MCP_STARTED = False


def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAI(timeout=90.0, max_retries=1)
    return _CLIENT


def _start_mcp_in_background() -> None:
    """Start the MCP servers off the turn path, once.

    THIS IS WHY A SIMPLE MESSAGE NO LONGER HANGS. build_team used to call
    manager().start() inline, which blocks while each server completes its
    handshake - and Playwright's FIRST run downloads a browser, minutes of it.
    So even "hey" sat on THINKING forever, because the turn could not reach the
    model until every server was up. Now the servers warm up on their own
    thread; the team is built immediately with whatever is ready, and each
    specialist picks up its MCP tools on the next turn after its server is up."""
    global _MCP_STARTED
    with _BUILD_LOCK:
        if _MCP_STARTED:
            return
        _MCP_STARTED = True

    def work() -> None:
        global _MCP_REPORT
        try:
            _MCP_REPORT = mcp_client.manager().start(mcp_config.server_specs())
        except Exception as e:
            state.record_error("agent.mcp_start", e)

    threading.Thread(target=work, daemon=True, name="mcp-start").start()


def build_team(force: bool = False) -> Agent:
    """Assemble the orchestrator. Cached, and does NOT wait on MCP.

    MCP servers warm up in the background; a specialist resolves its MCP tools
    per run, so a server that is not up yet is simply absent now and present on
    a later turn. The team is never blocked on a server, and a server that
    never starts just means that specialist falls back to run_python."""
    global _TEAM
    with _BUILD_LOCK:
        if _TEAM is not None and not force:
            built = _TEAM
        else:
            built = None
    _start_mcp_in_background()
    if built is not None:
        return built

    client = _client()

    def sub(name, role, group, extra_tools):
        return Agent(name, models.BRAIN, "medium", _sub_prompt(role),
                     [RUN_PYTHON] + extra_tools, MAX_ROUNDS_SUB,
                     mcp_groups=[group])

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
    orch_tools += [PLAN, RUN_PYTHON, REMEMBER, RECALL, SCHEDULE_JOB, ASK_BOSS,
                   MESSAGE_BOSS]

    team = Agent("jarvis", models.BRAIN_HEAVY, "medium",
                 ORCH_PROMPT, orch_tools, MAX_ROUNDS_ORCH)
    with _BUILD_LOCK:
        _TEAM = team
    return team


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

    # History first, so "yes, proceed" knows what it is proceeding with. The
    # fresh state (date, facts, this contact) is appended for THIS turn and
    # passed in, never written onto the shared cached orchestrator - two
    # overlapping turns (a call and a Telegram line at once) would otherwise
    # race on its instructions field.
    history = brain._recent_turns(channel)
    # INTELLIGENT MEMORY: pull the memories that bear on THIS request (by
    # meaning, not recency) into the prompt, so the brain answers from what it
    # actually knows rather than only the last few lines.
    instructions = (team.instructions + state.facts_block()
                    + memory.recall_block(text)
                    + (state.as_context(jid) if jid else ""))
    reply = team.run(text, ctx, _client(), history=history,
                     instructions=instructions)

    reply = brain.vet(reply, ctx.evidence) or "[guess] Done."
    emit({"type": "reply", "text": reply})
    brain._remember_turn(channel, "out", reply)
    # Learn from the exchange, off the critical path (embeds + stores in a
    # background thread; never delays the reply).
    memory.capture_turn(text, reply, source=transport.value)
    return reply


# ─────────────────────────────────────────────────────────────────────────────
# Voice bridge — the phone and desk mic hand the WHOLE request to the terra
# orchestrator, so speech gets the same specialists, memory and honesty as text.
#
# The realtime model is ears and mouth, not the brain: it decides only whether
# a line is small talk it can answer itself, or a real request to hand over. One
# tool, so its schema stays tiny and cheap on an open audio line.
# ─────────────────────────────────────────────────────────────────────────────

VOICE_TOOLS = [{
    "type": "function",
    "name": "handle_request",
    "description": (
        "Hand the Boss's request to Jarvis's full brain - the one that can "
        "open apps, drive the browser, read files, message people and run "
        "code. Use this for ANYTHING that needs doing or looking up; only skip "
        "it for pure small talk. Before calling it, say one short line out loud "
        "about what you are about to do, so the line is not silent while it "
        "works. Speak back exactly what it returns."),
    "parameters": {
        "type": "object",
        "properties": {"request": {
            "type": "string",
            "description": "The Boss's request, in his words."}},
        "required": ["request"],
    },
}]


def voice_dispatcher(transport: Transport, emit):
    """A (name, args) -> dict callable for a realtime session.

    handle_request runs the orchestrator through run_turn, which owns memory
    and the daily cap - so the realtime session is told not to persist turns
    itself (owns_memory=False), and there is one record of the conversation,
    not two."""
    def dispatch(name: str, args: dict) -> dict:
        if name == "handle_request":
            reply = run_turn(args.get("request", ""), transport, emit=emit,
                             channel=f"chan:{transport.value}")
            return {"ok": True, "spoken": reply}
        return {"ok": False, "error": f"voice has no tool named {name!r}"}
    return dispatch
