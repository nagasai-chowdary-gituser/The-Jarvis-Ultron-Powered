"""
Tests for the multi-agent core - the parts that, when they broke, would cost
something real: the safety gate on every tool call, the MCP classification,
the deletion preflight, the daily cap, and that the agent loop actually runs a
tool and then answers.

All offline. No API key, no network, no MCP server - the model is faked and
MCP calls are never reached, because the gate and the classifier are pure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import agent_core  # noqa: E402
import mcp_config  # noqa: E402
import safety  # noqa: E402
import state  # noqa: E402
from safety import Blocked, Session, Transport  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    p = tmp_path / "t.db"
    monkeypatch.setattr(state, "DB_PATH", p)
    monkeypatch.setattr(state, "_ready", set(), raising=False)
    return p


# ── a fake Responses client ──────────────────────────────────────────────────

class _FC:
    type = "function_call"

    def __init__(self, name, args, cid):
        self.name = name
        self.arguments = json.dumps(args)
        self.call_id = cid

    def model_dump(self, **k):
        return {"type": "function_call", "name": self.name,
                "arguments": self.arguments, "call_id": self.call_id}


class _Resp:
    def __init__(self, output, text=""):
        self.output = output
        self.output_text = text
        self.usage = None


class FakeClient:
    """Plays a scripted sequence of model responses."""

    def __init__(self, script):
        self.script = script
        self.i = 0
        self.responses = self

    def create(self, **kwargs):
        r = self.script[self.i]
        self.i += 1
        return r


def _ctx(transport=Transport.DESK, confirmed=False):
    return agent_core.Ctx(Session(transport), lambda e: None, "chan:test",
                          confirmed)


# ── the deletion preflight ─────────────────────────────────────────────────

@pytest.mark.parametrize("code", [
    "import os; os.remove('a')",
    "import shutil; shutil.rmtree('a')",
    "from pathlib import Path; Path('a').unlink()",
    "import os; getattr(os,'remove')('a')",
    "from shutil import rmtree as nuke; nuke('a')",
    "exec('import os')",
    "import subprocess; subprocess.run('rm -rf /x', shell=True)",
    "import os; os.system('powershell Remove-Item x')",
])
def test_deletion_is_refused_on_every_route(code):
    with pytest.raises(Blocked):
        safety.refuse_destructive_code(code)


@pytest.mark.parametrize("code", [
    "open('n.txt','w').write('x')",
    "import shutil; shutil.move('a','b')",
    "import os; print(os.getcwd())",
])
def test_ordinary_code_and_moves_are_allowed(code):
    safety.refuse_destructive_code(code)        # must not raise


# ── the gate ─────────────────────────────────────────────────────────────────

def test_run_python_deletion_is_refused_by_the_gate(db):
    held = agent_core.guard(agent_core.RUN_PYTHON,
                            {"code": "import os; os.remove('x')", "why": "t"},
                            _ctx())
    assert held and held.get("status") == "refused"


def test_messaging_sends_without_confirmation(db):
    """The owner's rule: when he asks Jarvis to message someone, just send it -
    do not ask. The confirm loop on the desk chat had no way to answer, so a
    message could never be sent."""
    assert agent_core.guard(agent_core.MESSAGE_BOSS, {"text": "hi"},
                            _ctx(Transport.TELEGRAM)) is None
    assert agent_core.guard(agent_core.WHATSAPP_SEND,
                            {"contact": "Kapcut", "text": "hi"},
                            _ctx(Transport.DESK)) is None


def test_mass_broadcast_still_needs_the_pin(db):
    """Messaging one person is free; messaging EVERYONE is the dangerous case
    and stays PIN-gated as a high-risk action, not a per-send prompt."""
    import safety
    assert safety.risk_of("message everyone in my contacts") == "external_broadcast"
    with pytest.raises(Blocked):
        safety.authorize("external_broadcast")


def test_ordinary_run_python_is_not_held(db):
    assert agent_core.guard(agent_core.RUN_PYTHON,
                            {"code": "print(1)", "why": "t"}, _ctx()) is None


# ── MCP classification ───────────────────────────────────────────────────────

def test_classify_delete_tool_is_refused():
    assert mcp_config.classify("fs", "delete_file", {"path": "a"})[0] == "refuse"


def test_classify_shell_deletion_in_args_is_refused():
    v, _ = mcp_config.classify("desktop", "run_powershell",
                               {"command": "Remove-Item x"})
    assert v == "refuse"


def test_classify_money_is_refused():
    assert mcp_config.classify("x", "do", {"q": "transfer money now"})[0] == "refuse"


def test_classify_install_asks_first():
    assert mcp_config.classify("d", "install_package", {"n": "x"})[0] == "confirm"


def test_classify_ordinary_read_is_allowed():
    assert mcp_config.classify("fs", "read_file", {"path": "a"})[0] == "allow"


# ── the agent loop ───────────────────────────────────────────────────────────

def test_loop_runs_a_tool_then_answers(db):
    calls = {"n": 0}

    def fake(args, ctx):
        calls["n"] += 1
        return {"ok": True, "text": "value=42"}

    tool = agent_core.Tool("probe", "d",
                           {"type": "object", "properties": {}}, fake)
    agent = agent_core.Agent("t", "m", "low", "inst", [tool], 5)
    script = [_Resp([_FC("probe", {}, "c1")]), _Resp([], "the answer is 42")]
    out = agent.run("go", _ctx(), FakeClient(script))
    assert calls["n"] == 1 and "42" in out


def test_loop_refuses_a_deleting_run_python_without_running_it(db):
    ctx = _ctx()
    agent = agent_core.Agent("t", "m", "low", "inst", [agent_core.RUN_PYTHON], 5)
    script = [_Resp([_FC("run_python",
                         {"code": "import os; os.remove('x')", "why": "t"}, "c1")]),
              _Resp([], "handled")]
    agent.run("delete x", ctx, FakeClient(script))
    assert any(f.get("status") == "refused" for f in ctx.evidence["failures"])


# ── the daily cap ────────────────────────────────────────────────────────────

def test_daily_cap_stops_work(db, monkeypatch):
    monkeypatch.setenv("DAILY_CAP_USD", "1.00")
    monkeypatch.setattr(state, "spend_today", lambda *a, **k: 5.0)
    reply = agent_core.run_turn("do something expensive", Transport.DESK)
    assert "budget" in reply.lower()


# ── voice bridge ─────────────────────────────────────────────────────────────

def test_voice_dispatcher_rejects_unknown_tool():
    d = agent_core.voice_dispatcher(Transport.PHONE, lambda e: None)
    r = d("nope", {})
    assert r["ok"] is False
