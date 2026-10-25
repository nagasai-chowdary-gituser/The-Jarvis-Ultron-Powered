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
import budget
import fastpath
import mcp_client
import mcp_config
import memory
import models
import recipes
import safety
import state
import verify
from safety import Blocked, Session, Transport

MAX_ROUNDS_ORCH = 24
MAX_ROUNDS_SUB = 18          # enough for a multi-step UI task without the boss retrying
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
        # Guards the session counters and evidence lists when several helpers
        # run in parallel - `calls += 1` is a read-modify-write, and a lost
        # increment would silently widen the runaway guard.
        self.lock = threading.Lock()

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

# Read-only tools with no shared state, safe to run several at once (different
# URLs, different files). Browser tools are NOT here: there is one page, so two
# concurrent reads of it would race. Writes are never here.
_READ_ONLY_FUNCS = {"web_search", "web_fetch", "recall", "stats", "processes",
                    "list_windows", "read_window", "list_apps"}
_READ_ONLY_MCP = {"read_file", "read_text_file", "read_multiple_files",
                  "get_file_info", "list_directory", "list_directory_with_sizes",
                  "directory_tree", "search_files", "list_allowed_directories"}


def _is_read_only(tool) -> bool:
    if tool is None:
        return False
    if tool.name in _READ_ONLY_FUNCS:
        return True
    return tool.kind == "mcp" and tool.name in _READ_ONLY_MCP


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
            instructions: str | None = None, context: str = "") -> str:
        """Run until the model stops asking for tools. Returns its final text.

        One loop for every agent - orchestrator and specialist alike. The
        orchestrator seeds `history` and a specialist gets a single task line.

        `context` is the per-turn memory (facts, preferences, recall). It goes
        in the INPUT as a leading developer message, NOT prepended to
        `instructions` - that is a pure cost fix with NO behaviour change: the
        model gets the identical content in the same developer channel, but the
        system prompt and the tool schemas now stay byte-identical every turn,
        so OpenAI's prefix cache covers them (~10% price) instead of re-billing
        the whole tool list each turn. The content the model reads is unchanged."""
        tools = self._tools()
        specs = [t.spec() for t in tools.values()]
        instr = instructions or self.instructions
        convo: list[dict] = []
        if context:
            convo.append({"role": "developer", "content": context})
        convo += list(history or [])
        convo.append({"role": "user", "content": task})
        effort = self.effort
        failures = 0

        # THE GOVERNOR. A turn ends on a REASON that is true about the work -
        # finished, blocked, out of time, going in circles, or the runaway
        # ceiling - never on a bare step count (budget.py exists to undo that
        # bug). Sized from the request, but never below this agent's own round
        # allowance, so a specialist handed a big job is not rationed to a
        # chat-sized budget.
        b = budget.for_request(task)
        b.runaway_rounds = max(b.runaway_rounds, self.max_rounds)
        b.runaway_actions = max(b.runaway_actions, safety.RUNAWAY_TOOL_CALLS)

        while True:
            verdict = b.verdict()
            if verdict.terminal:
                return (b.explain(verdict)
                        or "I ran as far as I usefully could and should hand "
                           "this back.")
            # The cross-agent ceiling: the orchestrator and every specialist it
            # calls share one session, so this guards the whole turn, not just
            # this loop.
            if ctx.session.calls >= safety.RUNAWAY_TOOL_CALLS:
                return "I hit my runaway ceiling - that is a loop on my side."
            b.round_started()
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
                return (resp.output_text or "").strip()   # the model is done

            round_failed = False
            made_progress = False
            for fc, result in self._execute(calls, ctx, tools):
                ok = bool(result.get("ok"))
                b.did(productive=ok)
                try:
                    args = json.loads(fc.arguments or "{}")
                except Exception:
                    args = {}
                # A loop is the SAME call with the SAME outcome, not just a
                # repeat: polling that returns something new is fine.
                outcome = "ok" if ok else str(
                    result.get("error") or result.get("status") or "fail")[:40]
                looped = b.loops.looping(fc.name, args, outcome)
                b.loops.note(fc.name, args, outcome)
                if ok and not looped:
                    if b.progress.record("state_change", fc.name):
                        made_progress = True
                if not ok:
                    round_failed = True
                    b.recovered()
                convo.append({
                    "type": "function_call_output", "call_id": fc.call_id,
                    "output": json.dumps(result, default=str)[:TOOL_OUTPUT_LIMIT]})

            if not made_progress:
                b.progress.barren_round()   # a whole round, nothing changed

            # RECOVER, don't just retry. First failure: buy more reasoning
            # (nearly free on luna, and a blind retry is not). Second failure in
            # a row: a blind third try is the definition of stuck, so force a
            # REFLECT-AND-REPLAN - name why it is failing and change approach,
            # not repeat the same call. This is what turns "it kept doing the
            # broken thing" into a different path.
            if round_failed:
                failures += 1
                if failures == 1 and effort != "high":
                    effort = "high"
                elif failures == 2:
                    convo.append({"role": "user", "content":
                        "That approach has now failed twice. Do NOT repeat the "
                        "same call. In one line say WHY it is failing, then take "
                        "a DIFFERENT approach - another tool, another specialist, "
                        "or a changed input. If it truly cannot be done, stop and "
                        "say so with the reason."})
            else:
                failures = 0        # a clean round resets the recovery ladder

    def _execute(self, calls, ctx: Ctx, tools: dict) -> list:
        """Run this round's tool calls, in parallel when they are independent.

        Two cases parallelise:
        - DISTINCT specialist calls - each is its own isolated loop on its own
          resource, so "check my CPU and count my files" runs both at once.
        - Several READ-ONLY calls (web_fetch of three URLs, read three files) -
          independent reads with no shared state, so they fetch together instead
          of one after another, which is most of the wait in a research task.
        Everything else - run_python, a write, the browser (one page), the same
        specialist twice - stays serial and in order, because order can matter
        (a read after a click must see the click)."""
        parsed = []
        for fc in calls:
            try:
                args = json.loads(fc.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            parsed.append((fc, args))

        names = [getattr(fc, "name", "") for fc, _ in parsed]
        distinct = len(set(names)) == len(names)
        agent_calls = distinct and all(
            tools.get(n) is not None and tools[n].kind == "agent" for n in names)
        read_calls = all(_is_read_only(tools.get(n)) for n in names)

        if len(parsed) > 1 and (agent_calls or read_calls):
            from concurrent.futures import ThreadPoolExecutor
            ctx.say({"type": "parallel", "calls": len(parsed),
                     "tools": names})
            with ThreadPoolExecutor(max_workers=min(4, len(parsed))) as ex:
                futs = [ex.submit(self._dispatch, getattr(fc, "name", ""),
                                  args, ctx, tools) for fc, args in parsed]
                results = [f.result() for f in futs]
            return list(zip([fc for fc, _ in parsed], results))

        return [(fc, self._dispatch(getattr(fc, "name", ""), args, ctx, tools))
                for fc, args in parsed]

    def _dispatch(self, name: str, args: dict, ctx: Ctx, tools: dict) -> dict:
        tool = tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"no tool named {name!r}"}

        sig = f"{name}:{json.dumps(args, sort_keys=True, default=str)[:160]}"
        with ctx.lock:
            try:
                safety.check(name, ctx.session, sig)   # repeat + runaway guard
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
    kind = args.get("kind", "")
    # A watched chat must reply quickly and a mission should advance steadily,
    # so both default to a short interval unless the caller set one. Other
    # kinds keep their given/60s cadence.
    default_every = {"watch_chat": 5, "mission": 10}.get(kind, 60)
    jid = state.add_job(kind, args.get("params") or {},
                        args.get("every_seconds") or default_every,
                        args.get("until"))
    ctx.evidence["job_ids"].append(jid)
    return {"ok": True, "job_id": jid,
            "note": f"job #{jid} is running in the worker process"}


def _delete_jobs(args, ctx) -> dict:
    """Delete a scheduled job and remove it from the live panel. 'which' is a
    job number, 'all', or a word that matches a job (its kind or a contact)."""
    import re
    which = str(args.get("which", "")).strip().lower()
    if which in ("all", "*", "everything", "every job", ""):
        return {"ok": True, "deleted": state.delete_all_jobs(), "which": "all"}
    m = re.search(r"\d+", which)
    if m:
        ok = state.delete_job(int(m.group()))
        return {"ok": ok, "deleted": 1 if ok else 0, "id": int(m.group())}
    # match by kind or a contact/text in params
    removed = 0
    for j in state.jobs():
        blob = (j.get("kind", "") + " " + str(j.get("params") or "")).lower()
        if which in blob:
            removed += state.delete_job(j["id"])
    return {"ok": removed > 0, "deleted": removed,
            "note": f"removed {removed} job(s) matching {which!r}"}


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


def _setup_login(args, ctx) -> dict:
    """Open Jarvis's own Chrome at a sign-in page so the Boss logs in once."""
    import browser
    url = (args.get("url") or "https://platform.openai.com/").strip()
    try:
        return browser.open_for_login(url)
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


def _desk_call(fn_name):
    """For lib.desktop functions that already return their own ok-dict."""
    def run(args, ctx) -> dict:
        from lib import desktop
        try:
            return getattr(desktop, fn_name)(**dict(args))
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


def _save_skill(args, ctx) -> dict:
    """Crystallize a run_python solution that just worked into a named skill, so
    next time it is one call instead of regenerated code. Rebuilds the team so
    the new skill is callable immediately."""
    out = recipes.save_skill(args.get("name", ""), args.get("description", ""),
                             args.get("code", ""),
                             phone_safe=bool(args.get("phone_safe")))
    if out.get("ok"):
        build_team(force=True)        # pick the new recipe up for the next turn
        ctx.say({"type": "tool_result", "name": "save_skill", "ok": True,
                 "detail": f"saved skill '{out['name']}'"})
    return out


def _make_recipe_tool(rec: dict) -> "Tool":
    """Expose one saved recipe to the orchestrator as a direct callable tool.
    Routing is semantic: the model picks it by its description."""
    schema = rec["schema"]
    name = schema["name"]
    desc = f"[saved skill] {schema.get('description', '')}"
    params = schema.get("params") or {"type": "object", "properties": {}}

    def run(args, ctx) -> dict:
        return recipes.run_skill(rec, args or {})

    return Tool(name, desc, params, run)


def _verify_outcome(args, ctx) -> dict:
    """PROVE a claimed outcome against the real world, with no model in the
    loop - a file is there and big enough, a process is running, a command
    exited zero. verify.py decides; this just routes. Returns the Evidence as a
    dict (ok + why + what was found), so the model reports what was MEASURED."""
    kind = (args.get("kind") or "").strip()
    target = (args.get("target") or "").strip()
    try:
        if kind == "file":
            ev = verify.file_exists(target, int(args.get("min_bytes") or 1),
                                    args.get("suffix") or "")
        elif kind == "file_contains":
            ev = verify.file_contains(target, args.get("contains") or "")
        elif kind == "dir":
            ev = verify.dir_has(target, int(args.get("count") or 1),
                                args.get("pattern") or "*")
        elif kind == "process":
            ev = verify.process_running(target, float(args.get("timeout") or 0))
        else:
            return {"ok": False, "error": f"unknown verify kind {kind!r}; use "
                    "file, file_contains, dir or process"}
        out = ev.as_dict()
        out["ok"] = ev.ok
        return out
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


VERIFY_OUTCOME = _t("verify_outcome",
    "PROVE an outcome against the real world, without a model - use this to back "
    "a claim instead of asserting it. kinds: file (target=path; optional "
    "min_bytes, suffix like '.pdf' - a 0-byte or wrong-type file FAILS), "
    "file_contains (target=path, contains=text), dir (target=path, optional "
    "count, pattern), process (target=name or pid). Returns what was actually "
    "found. If you say you downloaded/created a file or started something, "
    "confirm it here first.",
    {"kind": {"type": "string",
              "enum": ["file", "file_contains", "dir", "process"]},
     "target": {"type": "string"},
     "min_bytes": {"type": "integer"}, "suffix": {"type": "string"},
     "contains": {"type": "string"}, "count": {"type": "integer"},
     "pattern": {"type": "string"}, "timeout": {"type": "number"}},
    ["kind", "target"], _verify_outcome)


RUN_PYTHON = _t("run_python",
    "Run Python in a fresh 60s subprocess (lib/ importable, workspace as cwd). "
    "The universal fallback for anything no other tool covers. Print what you "
    "need to see. Deletion and runtime-built code are refused.",
    {"code": {"type": "string"},
     "why": {"type": "string", "description": "One line: what this is for."}},
    ["code", "why"], _run_python)

SAVE_SKILL = _t("save_skill",
    "Crystallize a run_python solution that WORKED into a named, reusable skill, "
    "so next time the same task is ONE call instead of regenerated code. Do this "
    "after you solved something non-trivial with run_python and expect it again "
    "(e.g. 'count my downloads', 'back up today's notes'). Give a lower_snake "
    "name, a one-line description of what it does, and the exact working code. "
    "Deletion and eval/exec/subprocess are refused. Leave phone_safe off unless "
    "it is safe to run from a spoofed phone call.",
    {"name": {"type": "string"},
     "description": {"type": "string"},
     "code": {"type": "string"},
     "phone_safe": {"type": "boolean"}},
    ["name", "description", "code"], _save_skill)

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


def _read_calendar(args, ctx) -> dict:
    from lib import google
    return google.calendar_upcoming(hours=int(args.get("hours") or 24))


def _read_email(args, ctx) -> dict:
    from lib import google
    return google.gmail_unread(int(args.get("count") or 8))


def _connect_google(args, ctx) -> dict:
    from lib import google
    return google.setup()


def _email_draft(args, ctx) -> dict:
    from lib import google
    return google.create_draft(args.get("to", ""), args.get("subject", ""),
                               args.get("body", ""))


def _email_send(args, ctx) -> dict:
    from lib import google
    return google.send_draft(args.get("draft_id", ""))


def _calendar_create(args, ctx) -> dict:
    from lib import google
    return google.calendar_create(
        args.get("title", ""), args.get("start", ""), args.get("end", ""),
        args.get("attendees") or None, args.get("description", ""))


READ_CALENDAR = _t("read_calendar",
    "Read the Boss's upcoming Google Calendar events. Needs Google connected; "
    "if not, the result says so - tell him to say 'connect google'.",
    {"hours": {"type": "integer"}}, [], _read_calendar)

READ_EMAIL = _t("read_email",
    "Read the Boss's unread inbox email (sender, subject, snippet). Needs "
    "Google connected; if not, the result says so.",
    {"count": {"type": "integer"}}, [], _read_email)

CONNECT_GOOGLE = _t("connect_google",
    "Start the ONE-TIME Google sign-in so Jarvis can read the Boss's calendar "
    "and inbox AND draft/send mail and create events. Opens a browser for him "
    "to approve once. Needs the OAuth client file in the Jarvis folder first.",
    {}, [], _connect_google)

EMAIL_DRAFT = _t("email_draft",
    "Compose an email as a DRAFT (it is NOT sent). Always do this first, show "
    "the draft to the Boss, and only send after he confirms. Returns a draft_id.",
    {"to": {"type": "string"}, "subject": {"type": "string"},
     "body": {"type": "string"}}, ["to", "subject", "body"], _email_draft)

EMAIL_SEND = _t("email_send",
    "Send a draft the Boss has CONFIRMED, by its draft_id (from email_draft). "
    "Never call this until he has seen the draft and said to send it.",
    {"draft_id": {"type": "string"}}, ["draft_id"], _email_send)

CALENDAR_CREATE = _t("calendar_create",
    "Create a calendar event. start/end are ISO datetimes (end optional, "
    "defaults to +1h). For an event with OTHER attendees, show it and get the "
    "Boss's ok first; an event only for him you may create directly.",
    {"title": {"type": "string"}, "start": {"type": "string"},
     "end": {"type": "string"},
     "attendees": {"type": "array", "items": {"type": "string"}},
     "description": {"type": "string"}},
    ["title", "start"], _calendar_create)

SCHEDULE_JOB = _t("schedule_job",
    "Hand work to the background worker. Returns a job id immediately. The ONLY "
    "way to do anything that waits or runs after this turn ends. kinds: "
    "mission (params.goal - a BIG, long-running job pursued over many steps for "
    "up to hours, with checkpointing and recovery, e.g. 'research X deeply and "
    "write me a full report' or 'keep handling Y until Z'; set `until` for a "
    "stop condition), agent_task (params.request - a smaller job run ONCE and "
    "reported), watch_chat (params.contact), reminder (params.text), poll_url "
    "(params.url), check_process (params.name). params.urgency='call' rings the "
    "Boss instead of messaging.",
    {"kind": {"type": "string",
              "enum": ["mission", "agent_task", "watch_chat", "reminder",
                       "poll_url", "check_process"]},
     "params": {"type": "object"},
     "every_seconds": {"type": "integer"},
     "until": {"type": "string"}},
    ["kind", "params"], _schedule_job)

DELETE_JOBS = _t("delete_jobs",
    "Delete a scheduled job and remove it from the live panel. 'which' is a job "
    "number, 'all' to clear every job, or a word matching a job (its kind or a "
    "contact). Use when the Boss says to stop/remove/delete a job or all jobs.",
    {"which": {"type": "string"}}, ["which"], _delete_jobs)

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

SETUP_LOGIN = _t("setup_login",
    "Open Jarvis's OWN Chrome at a sign-in page so the Boss logs in ONCE (it "
    "then persists for every future task). Use this when a page is signed out, "
    "shows a login screen, or lands on 'guest' - do NOT report the task done in "
    "that case. Also use it when he asks to set up login for a site. Pass the "
    "site url.",
    {"url": {"type": "string"}}, [], _setup_login)

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

OPEN_CHROME_PROFILE = _t("open_chrome_profile",
    "Open the Boss's REAL Chrome in a SPECIFIC profile (he has many Google "
    "accounts). 'which' can be 'main', an ordinal by picker order ('2nd', "
    "'fifth', '5'), or part of the account name or email ('rudrxai'). If it "
    "cannot tell which, the result lists the profiles - show them and ask.",
    {"which": {"type": "string"}}, ["which"], _desk_call("open_chrome_profile"))

MEDIA = _t("media", "Media keys: play|pause|next|previous|volumeup|volumedown|mute.",
    {"action": {"type": "string"}}, ["action"],
    _desktop(lambda d, a: d.media(a.get("action", ""))))

PROCESSES = _t("processes", "List running processes, optionally filtered.",
    {"filter": {"type": "string"}}, [],
    _desktop(lambda d, a: d.processes(a.get("filter", ""))))

STATS = _t("stats", "This machine's CPU, memory and disk right now.",
    {}, [], _desktop(lambda d, a: d.stats()))

LIST_WINDOWS = _t("list_windows",
    "Open window titles - cheaper than a screenshot, and the title often holds "
    "the answer.", {}, [], _desk_call("windows"))

READ_WINDOW = _t("read_window",
    "Read a native app window's interactive controls (buttons, fields, list and "
    "menu items) by NAME and type, so you can see what is in it. Use this "
    "instead of a screenshot or OCR - it is the accessibility tree, not pixels.",
    {"title": {"type": "string",
               "description": "part of the window title, e.g. 'Settings'"}},
    ["title"], _desk_call("read_window"))

CLICK_CONTROL = _t("click_control",
    "Click a control in a native app by its accessibility NAME (as read_window "
    "shows it), never a coordinate. Works for buttons, menu items, list items.",
    {"title": {"type": "string"}, "control": {"type": "string"}},
    ["title", "control"], _desk_call("click_control"))

CLICK_SEQUENCE = _t("click_sequence",
    "Click SEVERAL named controls in order in ONE call - the fast way to do a "
    "multi-step UI action. e.g. click_sequence('Calculator', ['One','Two',"
    "'Three','Plus','Four','Equals']). Read the names once with read_window, "
    "then send them all here instead of one click per step.",
    {"title": {"type": "string"},
     "controls": {"type": "array", "items": {"type": "string"}}},
    ["title", "controls"], _desk_call("click_sequence"))

SET_CONTROL = _t("set_control",
    "Type text into a named field of a native app (sets the value directly).",
    {"title": {"type": "string"}, "control": {"type": "string"},
     "text": {"type": "string"}},
    ["title", "control", "text"], _desk_call("set_control"))


def _see_screen(args, ctx) -> dict:
    from lib import vision
    return vision.see_screen(args.get("question", ""), args.get("window", ""))


def _see_image(args, ctx) -> dict:
    from lib import vision
    return vision.see_image(args.get("path", ""), args.get("question", ""))


SEE_SCREEN = _t("see_screen",
    "LOOK at the screen with your eyes - for anything with no accessibility "
    "tree: an image, a chart, a design, a game, a video frame, a scanned doc. "
    "Pass a window title to look at just that window. Ask what you need to "
    "know. For ordinary app text or web pages, prefer read_window / the browser "
    "- they are exact and free; use eyes only when there is nothing structured "
    "to read.",
    {"question": {"type": "string"},
     "window": {"type": "string",
                "description": "part of a window title, or omit for the whole screen"}},
    [], _see_screen)

SEE_IMAGE = _t("see_image",
    "Look at an image or a PDF file and answer a question about it.",
    {"path": {"type": "string"}, "question": {"type": "string"}},
    ["path"], _see_image)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_IDENTITY = """You are Jarvis, a personal agent on your owner's own Windows machine. He is the only person you take instructions from, and you call him Boss. You are Jarvis - never ChatGPT, never Claude, never "an assistant helping a friend"; if asked who you are, you are Jarvis. Never name the model you run on."""

_HONESTY = """WHAT COUNTS AS TRUE
Say what a tool RETURNED, never what you intended. A message is sent when the send returned an id; a file exists when the write returned its size. If a tool failed, say it failed and why. If you did not read something, you do not know it. Never say you are monitoring or watching unless schedule_job returned an id THIS turn. The database word "pending" means a job is ACTIVE and queued, not broken - do not tell the Boss an active job is dead.
MEASURE, DON'T ASSERT. Before you claim you downloaded or created a file, that something is running, or that a command succeeded, PROVE it with verify_outcome and report what it actually found - a file that is missing, empty, or the wrong type has NOT been produced. Do not report such an outcome done on the strength of a tool you hoped worked."""

ORCH_PROMPT = _IDENTITY + """

PLAN FIRST, THEN MOVE FAST
If the task needs DOING something (opening apps, the browser, files, messaging, several steps), call plan(task, helpers, steps) - name the task in a few words, the helpers/MCP you will use, the short steps. To save a round, emit plan AND your first helper call IN THE SAME STEP (plan runs instantly and the first helper starts at once). Then carry it out step by step, no pausing and no second-guessing. If it is a greeting, a chat line, or a question you can answer from what you know or one quick tool, SKIP the plan and just answer - do not plan a plan for a one-liner.

HOW YOU WORK
You lead a small team of specialists and you decide who does what. Hand a job to a specialist by calling it with one clear sentence:
  files    - read, write, organise files in the workspace
  browser  - drive Chrome: open pages, read them, click, fill, check what is on screen (call ensure_browser first)
  desktop  - LAUNCH apps, media keys, clipboard, list processes, window control. OS-level only.
  research - search the web and read pages
  comms    - WhatsApp and Telegram. It OPENS the app itself, finds the contact safely over the app's own protocol, sends, and verifies. When the Boss tells you to message someone, SEND IT - do not ask again. Only refuse to message people on your own, unprompted.
  coder    - read/write code and work with GitHub
For anything a specialist does not cover, use run_python yourself. Prefer a specialist over writing code when one fits - it can see the machine between steps, which run_python cannot. If a saved skill (a tool marked "[saved skill]") already does what is asked, just call it. When run_python solves something non-trivial that the Boss is likely to want again, call save_skill to crystallize it, so next time it is one call.

ROUTING - this is where turns go wrong, so get it right:
- Messaging ANYONE on WhatsApp or Telegram is ONE comms call, and ONLY comms. comms opens the app itself, so NEVER send "open whatsapp" to desktop as a separate step, and never try to drive a chat app any other way. "open whatsapp and message Kap Cut hi" = one comms call: "send the WhatsApp message 'hi' to Kap Cut".
- Do NOT split one action across specialists, and never hand the same job to two of them.
- When a specialist reports it DID the job, the job is DONE - speak its result and stop. Do not re-do it with another specialist or with run_python to "double-check"; that is how a real success gets reported as a failure.
- Reading what is on a web or chat app's screen (WhatsApp, Gmail, a website) is comms/browser work over the app's protocol - NEVER screenshots or OCR.
- GO FAST: when parts of the task are INDEPENDENT (e.g. check the CPU and count files, or research one thing while reading another), call those specialists in the SAME step - they run at the same time instead of one after another.

""" + _HONESTY + """

FINISH THE WHOLE REQUEST
Three things asked means three things done. Do not stop between parts to check in. If one part is genuinely impossible, do the rest and say which part failed and why. Ask the Boss (ask_boss) only when a name or instruction is truly ambiguous, and ask once.

ANYTHING THAT WAITS IS A JOB
Never sleep or "keep an eye" in a turn. If something must be watched or continued until a condition, call schedule_job - it returns an id and a separate worker does the waiting.

HOW YOU SPEAK
Short. One or two sentences, no markdown, no bullet points - it is often read aloud. Numbers as a person would say them. Put detail on the screen, keep speech terse."""


_ROLE_RULES = {
    "communications specialist":
        "WhatsApp: call whatsapp_send(contact, text) - it opens WhatsApp, finds "
        "the contact safely over CDP, sends, and reads the message back as "
        "proof. To message the Boss himself, use message_boss. NEVER drive a "
        "chat app with run_python/screenshots/OCR/key-presses. If a name "
        "matches more than one chat, report the options; do not guess. "
        "EMAIL is DRAFT-FIRST, always: call email_draft(to, subject, body), "
        "SHOW the Boss the draft, and STOP - call email_send(draft_id) only "
        "after he explicitly says to send. Never auto-send to a client or an "
        "important person without his ok. CALENDAR: an event only for the Boss "
        "you may create with calendar_create directly; an event with other "
        "attendees, show it and confirm first. You never delete an email or an "
        "event - there is no tool for it.",
    "desktop specialist":
        "You control the machine and its NATIVE apps. open_app launches; media "
        "is the media keys; clipboard, processes, stats for the system. To "
        "operate a native app (Settings, File Explorer, Notepad, a dialog): "
        "call read_window ONCE to see its controls by name, then act on them BY "
        "NAME with click_sequence (several buttons/menu items in one call) and "
        "set_control (fill a field) - the accessibility tree, so never "
        "screenshots, never OCR, never pixel coordinates, and never one click "
        "per turn. For genuinely complex logic you may write one run_python "
        "using the same lib.desktop helpers. WEB or chat apps (WhatsApp, Gmail, "
        "a website) are the "
        "comms or browser specialist's job, not yours - hand those back.",
    "research specialist":
        "Search with web_search, then read pages with web_fetch. When you need "
        "to read SEVERAL pages, request them in ONE step (several web_fetch "
        "calls together) so they load at the same time instead of one after "
        "another. Report only what the pages actually said.",
    "browser specialist":
        "Call ensure_browser once, then browser_navigate to the page. To READ a "
        "page, take ONE browser_snapshot, or ONE browser_evaluate that returns "
        "exactly the text you need (titles, rows) - do NOT snapshot or evaluate "
        "the same page several times; get what you need in as few calls as "
        "possible. Click and type by the page's structure (role/text), never "
        "pixels or screenshots. IF THE PAGE IS SIGNED OUT - a login screen, a "
        "'guest' state, or it asks the Boss to sign in - you are in Jarvis's "
        "own Chrome, which needs a one-time sign-in: call setup_login(url) and "
        "tell the Boss to sign in once in the Jarvis Chrome window. Do NOT "
        "report the task done, and never pretend you saw his account. "
        "IF THE PAGE IS A CAPTCHA OR HUMAN-VERIFICATION CHALLENGE - 'verify you "
        "are human', 'I'm not a robot', 'checking your browser', 'unusual "
        "traffic' - you CANNOT and MUST NOT try to solve it, and you must never "
        "claim you got past one. Stop and report BLOCKED: the page needs the "
        "Boss himself. IF A PAGE DOES NOT MATCH WHAT YOU EXPECTED (it changed, "
        "a selector finds nothing), re-read it ONCE to see what is actually "
        "there and adapt - do not repeat the same failing click.",
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
        # MEDIUM, not low. low was measured faster but dumber - asked for CPU
        # usage it ran platform.processor() and returned the chip's model name.
        # The owner's bar is "not dumb", and the fast path already answers the
        # instant common things with no model at all, so the model path that is
        # left is the non-trivial work that must be done RIGHT. Escalates to
        # high on a failed round.
        return Agent(name, models.BRAIN, "medium", _sub_prompt(role),
                     [RUN_PYTHON] + extra_tools, MAX_ROUNDS_SUB,
                     mcp_groups=[group])

    files = sub("files", "files specialist", "files", [VERIFY_OUTCOME])
    browser = sub("browser", "browser specialist", "browser",
                  [ENSURE_BROWSER, SETUP_LOGIN, VERIFY_OUTCOME])
    desktop = sub("desktop", "desktop specialist", "desktop",
                  [OPEN_APP, OPEN_CHROME_PROFILE, MEDIA, PROCESSES, STATS,
                   LIST_WINDOWS, READ_WINDOW, CLICK_CONTROL, CLICK_SEQUENCE,
                   SET_CONTROL, SEE_SCREEN, SEE_IMAGE, VERIFY_OUTCOME])
    research = sub("research", "research specialist", "research",
                   [WEB_SEARCH, WEB_FETCH, READ_CALENDAR, READ_EMAIL])
    comms = sub("comms", "communications specialist", "comms",
                [WHATSAPP_SEND, MESSAGE_BOSS, EMAIL_DRAFT, EMAIL_SEND,
                 CALENDAR_CREATE, READ_EMAIL, READ_CALENDAR])
    coder = sub("coder", "coding specialist", "coder", [VERIFY_OUTCOME])

    orch_tools = [agent_as_tool(a, client) for a in
                  (files, browser, desktop, research, comms, coder)]
    orch_tools += [PLAN, RUN_PYTHON, SAVE_SKILL, VERIFY_OUTCOME, REMEMBER,
                   RECALL, SCHEDULE_JOB, DELETE_JOBS, ASK_BOSS, MESSAGE_BOSS,
                   READ_CALENDAR, READ_EMAIL, CONNECT_GOOGLE, SEE_SCREEN]
    # Crystallized skills become first-class tools - the whole point of saving
    # one is that next time it is a direct call, routed by its description. A
    # broken recipe file is skipped (parsed, not imported), never fatal.
    try:
        orch_tools += [_make_recipe_tool(r) for r in recipes.all_skills()]
    except Exception as e:
        state.record_error("agent.load_recipes", e)

    # terra at medium: routing and the final answer are where a wrong call
    # shows as dumbness, so this stays smart. The fast path carries the
    # latency-critical common requests with no model call at all.
    team = Agent("jarvis", models.BRAIN_HEAVY, "medium",
                 ORCH_PROMPT, orch_tools, MAX_ROUNDS_ORCH)
    with _BUILD_LOCK:
        _TEAM = team
    return team


def mcp_report() -> dict:
    """Live MCP state, read from the manager so it reflects servers as they come
    up (the cached _MCP_REPORT summary lags a hair behind a parallel start)."""
    srvs = mcp_client.manager().servers
    if srvs:
        return {name: {"ok": s.alive, "tools": len(s.tools), "error": s.error}
                for name, s in srvs.items()}
    return _MCP_REPORT


# ─────────────────────────────────────────────────────────────────────────────
# The entry the doors call — one message in, one reply out.
# ─────────────────────────────────────────────────────────────────────────────

import re as _re

_SOURCE_TAG = _re.compile(
    r"^\s*\[(?:state|guess|whatsapp|telegram|phone|desk|web|file|job #\d+)\]\s*",
    _re.I)


def _strip_source_tag(text: str) -> str:
    cleaned = _SOURCE_TAG.sub("", text or "").strip()
    return cleaned or (text or "").strip()


def _is_trivial(text: str) -> bool:
    """A one-liner that needs no checking: the small budget tier. Sizing the
    gate off budget.for_request keeps 'what needs verifying' in one place."""
    return budget.for_request(text).deadline_s <= 60.0


def _verify_and_repair(text: str, reply: str, team, ctx: Ctx,
                       history: list, context: str, emit) -> str:
    """One self-check, and at most one repair. Returns the final reply.

    Fires only when a tool actually ran (so pure chat and read-only answers the
    model is confident about are not taxed) and the request is not trivial.
    verify.check is conservative, so a gap here is a real one; we hand exactly
    that gap back and let the orchestrator finish it, rather than re-doing the
    whole task."""
    did_work = bool(ctx.evidence["results"] or ctx.evidence["failures"])
    if not did_work or _is_trivial(text):
        return reply
    v = verify.check(text, reply, ctx.evidence, client=_client())
    if v["ok"]:
        emit({"type": "tool", "name": "verify", "code": "", "why": "checking"})
        emit({"type": "tool_result", "name": "verify", "ok": True,
              "detail": "checks out"})
        return reply

    emit({"type": "tool", "name": "verify", "code": "", "why": v["gap"][:80]})
    repair = (f"Self-check: you reported this done, but a gap remains - {v['gap']}. "
              f"Do ONLY that part now with your tools, then give the final reply. "
              f"If it genuinely cannot be done, say so plainly and why.")
    seeded = list(history) + [{"role": "user", "content": text},
                              {"role": "assistant", "content": reply}]
    try:
        fixed = team.run(repair, ctx, _client(), history=seeded,
                         context=context)
        fixed = _strip_source_tag(brain.vet(fixed, ctx.evidence) or reply)
    except Exception as e:
        state.record_error("agent.repair", e)
        fixed = reply
    emit({"type": "tool_result", "name": "verify", "ok": True,
          "detail": "repaired: " + v["gap"][:120]})
    return fixed or reply


def run_turn(text: str, transport: Transport, *, jid: str = "", emit=None,
             channel: str = "", user_confirmed: bool = False) -> str:
    """Run one turn through the orchestrator. Never raises."""
    import os
    emit = emit or (lambda ev: None)
    channel = channel or f"chan:{transport.value}"
    brain._remember_turn(channel, "in", text)

    # THE FAST PATH - the single biggest "within seconds" win. "open chrome",
    # "what's my cpu", "pause", "what time is it" are unmistakable: they need
    # DOING, not planning. Deterministic code answers them in milliseconds with
    # no model call at all. Anything ambiguous, multi-step, or that sends/
    # deletes/spends falls through to the orchestrator (fastpath is read-only or
    # trivially reversible by design).
    quick = fastpath.try_run(text)
    if quick is not None:
        say = quick.get("say", "Done.")
        emit({"type": "tool", "name": "fastpath", "code": "",
              "why": text[:60]})
        emit({"type": "tool_result", "name": "fastpath", "ok": True,
              "detail": say[:150]})
        emit({"type": "reply", "text": say})
        brain._remember_turn(channel, "out", say)
        # No capture_turn here: the fast path only ever answers deterministic,
        # trivial ops ("open chrome", "what's my cpu", "pause") that teach
        # nothing durable, so the background learning call was pure spend. The
        # turn is still remembered in the channel history above.
        return say

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
    # actually knows rather than only the last few lines. This goes in as the
    # per-turn CONTEXT (a leading developer message), NOT glued onto the system
    # prompt - identical content for the model, but the system prompt and tool
    # schemas stay byte-identical so the prefix cache covers them.
    context = (state.facts_block()
               + memory.preferences_block()             # how he likes things done
               + memory.recall_block(text)
               + (state.as_context(jid) if jid else ""))
    reply = team.run(text, ctx, _client(), history=history, context=context)

    reply = brain.vet(reply, ctx.evidence) or "Done."
    # vet tags the source ([state]/[guess]) for its own honesty checks; strip
    # that prefix from what the Boss actually sees - a final-boss assistant
    # answers cleanly, it does not prepend "[guess]" to every line. The honesty
    # is already structural (a false claim was caught above, not hidden here).
    reply = _strip_source_tag(reply)

    # CHECK ITS OWN WORK before reporting done. Only on real DOING turns (a
    # tool actually ran) and never on the trivial tier - a verify pass on a
    # greeting is wasted money, and a false "not done" turns a real success
    # into a retry, so verify.check is deliberately conservative. Bounded to
    # ONE repair: if the check finds a concrete gap, hand that one gap back and
    # let the orchestrator finish it, then the answer stands. This is the piece
    # budget.py was always designed around.
    reply = _verify_and_repair(text, reply, team, ctx, history, context, emit)

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
