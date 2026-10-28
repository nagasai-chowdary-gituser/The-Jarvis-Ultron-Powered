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

def test_whatsapp_read_surfaces_the_latest_incoming(db, monkeypatch):
    """Replying needs reading. whatsapp_read must hand back what the other
    person actually said, so the model can answer it."""
    from lib import whatsapp
    monkeypatch.setattr(whatsapp, "history", lambda c, n: [
        {"from": "me", "mine": True, "raw": "hi", "text": "hi"},
        {"from": "Kap Cut", "mine": False, "raw": "pay me the 10000",
         "text": "pay me the 10000"},
    ])
    out = agent_core._whatsapp_read({"contact": "Kap Cut"}, _ctx())
    assert out["ok"] and out["count"] == 2
    assert out["latest_incoming"] == "pay me the 10000"


def test_whatsapp_read_reports_a_real_failure_not_silence(db, monkeypatch):
    from lib import whatsapp

    def boom(c, n):
        raise RuntimeError("port 9223 down")
    monkeypatch.setattr(whatsapp, "history", boom)
    out = agent_core._whatsapp_read({"contact": "X"}, _ctx())
    assert out["ok"] is False and "port" in out["error"].lower()


# ── it knows what it did, makes real files, and is honest about calling ───────

def test_make_pptx_writes_exactly_the_slides_asked_for(tmp_path):
    out = agent_core._make_pptx(
        {"title": "2026 Nepal Floods",
         "filename": str(tmp_path / "nepal.pptx"),
         "slides": [{"title": "Overview", "bullets": ["heavy monsoon", "flooding"]},
                    {"title": "Impact", "bullets": ["displaced families"]},
                    {"title": "Response"},
                    {"title": "Outlook", "bullets": ["recovery", "aid"]}]},
        _ctx())
    assert out["ok"] and out["slides"] == 4          # four means FOUR, one file
    p = Path(out["path"])
    assert p.exists() and p.stat().st_size > 0        # actually saved to disk


def test_make_docx_writes_a_real_saved_word_file(tmp_path):
    out = agent_core._make_docx(
        {"title": "Flood Response Plan",
         "filename": str(tmp_path / "plan.docx"),
         "sections": [{"heading": "Summary", "text": "Monsoon flooding hit hard."},
                      {"heading": "Actions", "bullets": ["evacuate", "supplies"]}]},
        _ctx())
    assert out["ok"]
    p = Path(out["path"])
    assert p.exists() and p.stat().st_size > 0        # actually saved to disk
    from docx import Document
    doc = Document(str(p))                             # re-open = proof it is real
    text = "\n".join(par.text for par in doc.paragraphs)
    assert "Flood Response Plan" in text and "evacuate" in text


def test_make_docx_accepts_a_plain_body(tmp_path):
    out = agent_core._make_docx(
        {"title": "Note", "filename": str(tmp_path / "note.docx"),
         "body": "line one\nline two"}, _ctx())
    assert out["ok"] and Path(out["path"]).exists()


def test_activity_log_lets_jarvis_answer_for_itself(db):
    state.record_action("make_pptx", True, 1800, "saved nepal.pptx",
                        channel="chan:test")
    state.record_action("run_python", False, 300, "FileNotFoundError",
                        channel="chan:test")
    out = agent_core._recent_activity({"limit": 10}, _ctx())
    assert out["ok"] and out["count"] == 2
    tools = [a["tool"] for a in out["actions"]]
    assert "make_pptx" in tools and "run_python" in tools
    assert out["failures"] and out["failures"][0]["tool"] == "run_python"
    assert out["total_seconds"] > 0                   # "how long" is answerable


def test_dispatch_writes_the_action_log(db):
    def probe(args, ctx):
        return {"ok": True, "text": "value=42"}
    tool = agent_core.Tool("probe", "d",
                           {"type": "object", "properties": {}}, probe)
    agent = agent_core.Agent("t", "m", "low", "inst", [tool], 5)
    script = [_Resp([_FC("probe", {}, "c1")]), _Resp([], "done")]
    agent.run("go", _ctx(), FakeClient(script))
    assert any(a["tool"] == "probe" and a["ok"] for a in state.recent_actions(5))


def test_call_me_is_honest_when_it_cannot_call(db, monkeypatch):
    from lib import phone

    def boom(reason):
        raise RuntimeError("Twilio or MY_NUMBER not configured")
    monkeypatch.setattr(phone, "call_me", boom)
    out = agent_core._call_me({"reason": "the deck is ready"}, _ctx())
    assert out["ok"] is False and "twilio" in out["error"].lower()


def test_call_me_reports_a_real_placed_call(db, monkeypatch):
    from lib import phone
    monkeypatch.setattr(phone, "call_me", lambda reason: {"sid": "CA123", "to": "+91…"})
    out = agent_core._call_me({"reason": "done"}, _ctx())
    assert out["ok"] is True and out["sid"] == "CA123"


# ── the real hand: full control, but guarded ──────────────────────────────────

def test_cursor_click_refuses_a_payment_control(db, monkeypatch):
    from lib import cursor
    clicked = []
    monkeypatch.setattr(cursor, "click", lambda *a, **k: clicked.append(a))
    out = agent_core._cursor_click({"x": 10, "y": 20, "label": "Buy now"}, _ctx())
    assert out["ok"] is False and out.get("status") == "blocked"
    assert not clicked                       # the click never happened


def test_cursor_click_refuses_a_delete_control(db, monkeypatch):
    from lib import cursor
    clicked = []
    monkeypatch.setattr(cursor, "click", lambda *a, **k: clicked.append(a))
    out = agent_core._cursor_click({"x": 1, "y": 1, "label": "Delete account"},
                                   _ctx())
    assert out["ok"] is False and not clicked


def test_cursor_click_proceeds_on_a_safe_label(db, monkeypatch):
    from lib import cursor
    clicked = []
    monkeypatch.setattr(cursor, "click", lambda x, y, **k: clicked.append((x, y)))
    out = agent_core._cursor_click({"x": 100, "y": 200, "label": "Skip Ad"},
                                   _ctx())
    assert out["ok"] is True and clicked == [(100, 200)]


def test_cursor_click_refuses_an_unlabelled_click(db, monkeypatch):
    # An empty label used to skip the guard entirely - a blind click must be
    # refused, so the pay/delete check can never be bypassed.
    from lib import cursor
    clicked = []
    monkeypatch.setattr(cursor, "click", lambda *a, **k: clicked.append(a))
    assert agent_core._cursor_click({"x": 10, "y": 20}, _ctx())["ok"] is False
    assert agent_core._cursor_click({"x": 10, "y": 20, "label": "   "},
                                    _ctx())["ok"] is False
    assert not clicked                       # nothing was ever clicked


def test_cursor_type_refuses_payment_details(db, monkeypatch):
    from lib import cursor
    typed = []
    monkeypatch.setattr(cursor, "type_text", lambda t: typed.append(t))
    out = agent_core._cursor_type({"text": "my card number is 4111 1111"}, _ctx())
    assert out["ok"] is False and not typed


def test_cursor_key_presses_a_chord(db, monkeypatch):
    from lib import cursor
    pressed = []
    monkeypatch.setattr(cursor, "press", lambda c: pressed.append(c))
    out = agent_core._cursor_key({"keys": "ctrl+a"}, _ctx())
    assert out["ok"] and pressed == ["ctrl+a"]


# ── the trio: eyes locate, hand clicks ────────────────────────────────────────

def _mock_grid(monkeypatch, fx, fy, fine=True):
    """Make _grid_pick return a cell box centred at (fx, fy) of whatever image it
    is given (Set-of-Marks grounding). `fine=False` makes the second (fine) pass
    return None, so the result is marked reliable=False."""
    from PIL import Image

    from lib import cursor, vision
    monkeypatch.setattr(vision, "_capture_screen",
                        lambda: Image.new("RGB", (1920, 1200)))
    monkeypatch.setattr(cursor, "screen_size", lambda: (1536, 960))
    calls = {"n": 0}

    def pick(img, target, cols, rows):
        calls["n"] += 1
        if calls["n"] >= 2 and not fine:
            return None
        w, h = img.size
        cw, ch = w / cols, h / rows
        cx, cy = w * fx, h * fy
        return (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)

    monkeypatch.setattr(vision, "_grid_pick", pick)


def test_locate_returns_bounded_coords_when_found(monkeypatch):
    from lib import vision
    _mock_grid(monkeypatch, 0.3, 0.4)
    out = vision.locate("the Skip Ad button")
    assert out["ok"] and out["found"] and out["reliable"] is True
    assert 0 <= out["x"] < 1536 and 0 <= out["y"] < 960   # inside the screen


def test_locate_coarse_only_is_marked_less_reliable(monkeypatch):
    # If the fine (zoom) pass can't pin it, the result is coarse-only -> reliable
    # False, so the caller treats it as approximate.
    from lib import vision
    _mock_grid(monkeypatch, 0.3, 0.4, fine=False)
    out = vision.locate("the button")
    assert out["found"] and out["reliable"] is False


def test_trio_loop_locate_then_guarded_click_then_look(db, monkeypatch):
    """End-to-end wiring of the trio: the eyes' coordinates flow into a GUARDED
    click, and the confirm glance runs. Model + OS stubbed; the coordinate flow,
    the guard, and the loop order are real."""
    from lib import cursor, vision
    _mock_grid(monkeypatch, 0.3, 0.3)
    loc = agent_core._locate_on_screen({"target": "the Skip Ad button"}, _ctx())
    assert loc["found"] and 0 <= loc["x"] < 1536 and 0 <= loc["y"] < 960

    clicked = []
    monkeypatch.setattr(cursor, "click", lambda x, y, **k: clicked.append((x, y)))
    clk = agent_core._cursor_click(
        {"x": loc["x"], "y": loc["y"], "label": "the Skip Ad button"}, _ctx())
    assert clk["ok"] and clicked == [(loc["x"], loc["y"])]   # eyes -> hand

    monkeypatch.setattr(vision, "see_screen",
                        lambda q="", w="": {"ok": True,
                                            "answer": "The video is playing, no ad."})
    look = agent_core._see_screen(
        {"question": "is the song actually playing or is an ad showing?"}, _ctx())
    assert look["ok"] and "playing" in look["answer"]


def test_trio_loop_guard_still_fires_on_a_located_payment_button(db, monkeypatch):
    """Even with a real located coordinate, a click LABELLED as a pay control is
    refused - the loop does not launder a dangerous target."""
    from lib import cursor, vision
    _mock_grid(monkeypatch, 0.3, 0.3)
    clicked = []
    monkeypatch.setattr(cursor, "click", lambda *a, **k: clicked.append(a))
    loc = agent_core._locate_on_screen({"target": "the purchase button"}, _ctx())
    clk = agent_core._cursor_click(
        {"x": loc["x"], "y": loc["y"], "label": "Buy now button"}, _ctx())
    assert clk["ok"] is False and not clicked        # refused, nothing clicked


def test_locate_reports_not_found_cleanly(monkeypatch):
    from PIL import Image

    from lib import vision
    monkeypatch.setattr(vision, "_capture_screen",
                        lambda: Image.new("RGB", (800, 600)))
    monkeypatch.setattr(vision, "_grid_pick", lambda img, t, c, r: None)
    out = vision.locate("a purple unicorn")
    assert out["ok"] and out["found"] is False


# ── review fixes: api bill, cursor yield, budget, no duplicate app ────────────

def test_api_usage_reads_the_local_spend_log(db):
    import state
    state.record_spend("gpt-5.6-terra", 100, 0, 50, 0.0100)
    state.record_spend("gpt-5.6-luna", 200, 0, 80, 0.0020)
    out = agent_core._api_usage({"period": "today"}, _ctx())
    assert out["ok"] and out["calls"] == 2
    assert abs(out["total_usd"] - 0.012) < 1e-6
    assert "terra" in out["report"]
    assert "no browser" in out["note"].lower()        # never the browser/API


def test_cursor_yields_when_the_owner_moves_the_mouse(monkeypatch):
    import pytest as _pytest

    from lib import cursor
    cursor._note_set(100, 100)                         # Jarvis left it at 100,100
    monkeypatch.setattr(cursor, "cursor_pos", lambda: (400, 400))  # owner moved it
    with _pytest.raises(cursor.UserActive):
        cursor._check_yield()


def test_cursor_does_not_yield_on_tiny_drift(monkeypatch):
    from lib import cursor
    cursor._note_set(100, 100)
    monkeypatch.setattr(cursor, "cursor_pos", lambda: (102, 101))  # within tolerance
    cursor._check_yield()                              # must NOT raise


def test_cursor_fresh_start_takes_control_without_yielding(monkeypatch):
    from lib import cursor
    cursor.reset()
    monkeypatch.setattr(cursor, "cursor_pos", lambda: (999, 999))
    cursor._check_yield()                              # no prior position -> no yield


def test_cursor_click_tool_reports_a_yield_cleanly(db, monkeypatch):
    from lib import cursor

    def boom(*a, **k):
        raise cursor.UserActive("you moved it")
    monkeypatch.setattr(cursor, "click", boom)
    out = agent_core._cursor_click({"x": 10, "y": 20, "label": "a button"}, _ctx())
    assert out["ok"] is False and out["status"] == "yielded"


def test_budget_gives_creative_tasks_real_time():
    import budget
    assert budget.for_request("draw it").deadline_s >= 400      # not the 60s tier
    assert budget.for_request("paint me a smiley").deadline_s >= 400


def test_open_app_does_not_launch_a_second_copy(monkeypatch):
    import subprocess

    from lib import desktop
    monkeypatch.setattr(desktop, "installed_apps",
                        lambda: [{"name": "Paint", "app_id": "Paint.App"}])
    monkeypatch.setattr(desktop, "_window_named", lambda name: True)  # already open
    launched = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda *a, **k: launched.append(a))
    out = desktop.open_app("paint")
    assert out["ok"] and out.get("already_open") and not launched   # no 2nd window


def test_fast_path_does_not_spend_on_learning(db, monkeypatch):
    """A fast-path turn answers a trivial, deterministic op - there is nothing
    durable to learn, so the background capture call must not fire."""
    monkeypatch.setattr(agent_core.fastpath, "try_run",
                        lambda t: {"say": "Opened Chrome."})
    calls = {"n": 0}
    monkeypatch.setattr(agent_core.memory, "capture_turn",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1))
    out = agent_core.run_turn("open chrome", Transport.DESK)
    assert out == "Opened Chrome." and calls["n"] == 0


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


def test_independent_specialists_run_in_parallel(db):
    """Two distinct specialist calls in one round overlap, results in order."""
    import time as _t
    order = []

    def mk(name):
        def fn(args, ctx):
            order.append(f"{name}-start")
            _t.sleep(0.25)
            order.append(f"{name}-end")
            return {"ok": True, "text": name}
        t = agent_core.Tool(name, "d", {"type": "object", "properties": {}}, fn)
        t.kind = "agent"
        return t

    agent = agent_core.Agent("o", "m", "low", "i", [mk("x"), mk("y")], 5)
    tools = agent._tools()

    class FC:
        def __init__(self, n):
            self.name = n; self.arguments = "{}"; self.call_id = n

    pairs = agent._execute([FC("x"), FC("y")], _ctx(), tools)
    assert [p[1]["text"] for p in pairs] == ["x", "y"]        # order preserved
    # They overlapped: y started before x finished.
    assert order.index("y-start") < order.index("x-end")


# ── context window: in-turn compaction + cross-turn rolling summary ───────────

def test_compact_convo_never_breaks_call_output_pairing(db):
    big = "x" * 4000
    convo = [{"role": "developer", "content": "ctx"},
             {"role": "user", "content": "go"}]
    for i in range(20):
        convo.append({"type": "function_call", "name": "t",
                      "arguments": "{}", "call_id": f"c{i}"})
        convo.append({"type": "function_call_output", "call_id": f"c{i}",
                      "output": big})
    n_items = len(convo)
    n_calls = sum(1 for m in convo if m.get("type") == "function_call")
    n_outs = sum(1 for m in convo if m.get("type") == "function_call_output")

    agent_core._compact_convo(convo)

    # NOTHING removed - every function_call still has its output beside it
    assert len(convo) == n_items
    assert sum(1 for m in convo if m.get("type") == "function_call") == n_calls
    assert sum(1 for m in convo if m.get("type") == "function_call_output") == n_outs
    outs = [m for m in convo if m.get("type") == "function_call_output"]
    # older outputs trimmed, the newest KEEP_WHOLE_OUTPUTS kept whole
    assert any("trimmed to save context" in m["output"]
               for m in outs[:-agent_core.KEEP_WHOLE_OUTPUTS])
    assert all(len(m["output"]) > 240
               for m in outs[-agent_core.KEEP_WHOLE_OUTPUTS:])


def test_compact_convo_leaves_a_small_turn_untouched(db):
    convo = [{"type": "function_call_output", "call_id": "c1", "output": "short"}]
    agent_core._compact_convo(convo)
    assert convo[0]["output"] == "short"


def test_rolling_summary_is_empty_for_a_short_chat(db):
    import brain
    import state
    state.add_message("chan:t", "in", "hi there")
    assert brain.rolling_summary("chan:t") == ""


def test_rolling_summary_recaps_a_long_chat(db, monkeypatch):
    import brain
    import state
    for i in range(30):
        state.add_message("chan:t", "in", f"message {i} about the Nepal project")
        state.add_message("chan:t", "out", f"noted {i}")

    class FakeResp:
        output_text = "Boss and Jarvis worked through the Nepal project plan."
        usage = None

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = self

        def create(self, **k):
            return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    monkeypatch.setattr(brain.models, "record", lambda *a, **k: None)
    out = brain.rolling_summary("chan:t")
    assert "Nepal project plan" in out and "EARLIER IN THIS CONVERSATION" in out


# ── cost: cacheable prompt, no behaviour change ───────────────────────────────

class _RecClient(FakeClient):
    def __init__(self, script):
        super().__init__(script)
        self.calls = []

    def create(self, **k):
        self.calls.append(k)
        return super().create(**k)


def test_memory_rides_in_input_so_the_prompt_stays_cacheable(db):
    """Per-turn memory must NOT be glued onto the system prompt: the model sees
    the same content (in the developer channel, in the input), but the
    instructions + tools stay byte-identical across turns so they cache."""
    agent = agent_core.Agent("t", "m", "low", "STATIC-SYSTEM-PROMPT", [], 3)

    c1 = _RecClient([_Resp([], "a")])
    agent.run("do a thing", _ctx(), c1, context="MEMORY_ALPHA")
    c2 = _RecClient([_Resp([], "b")])
    agent.run("do a thing", _ctx(), c2, context="MEMORY_BETA")

    i1, i2 = c1.calls[0], c2.calls[0]
    # the cacheable prefix is identical across two turns with different memory
    assert i1["instructions"] == i2["instructions"] == "STATIC-SYSTEM-PROMPT"
    assert "MEMORY_ALPHA" not in i1["instructions"]
    # and the memory still reaches the model, in the input, as developer context
    lead = i1["input"][0]
    assert lead["role"] == "developer" and lead["content"] == "MEMORY_ALPHA"
    assert "MEMORY_BETA" in json.dumps(i2["input"])


def test_no_context_leaves_the_input_unchanged(db):
    """A specialist (no context) must behave exactly as before: just the task."""
    agent = agent_core.Agent("t", "m", "low", "P", [], 3)
    c = _RecClient([_Resp([], "ok")])
    agent.run("the task", _ctx(), c)
    inp = c.calls[0]["input"]
    assert inp == [{"role": "user", "content": "the task"}]


# ── recover, don't just retry (#gap 2) ────────────────────────────────────────

def test_repeated_failure_forces_a_replan(db):
    """Two failed rounds in a row must inject a reflect-and-replan nudge, not a
    blind third retry of the same call."""
    def flaky(args, ctx):
        return {"ok": False, "error": "nope"}

    tool = agent_core.Tool("flaky", "d",
                           {"type": "object", "properties": {}}, flaky)
    agent = agent_core.Agent("t", "m", "low", "inst", [tool], 6)
    script = [_Resp([_FC("flaky", {}, "c1")]),
              _Resp([_FC("flaky", {}, "c2")]),
              _Resp([], "stopped cleanly")]

    class Rec(FakeClient):
        def __init__(self, s):
            super().__init__(s)
            self.inputs = []

        def create(self, **k):
            self.inputs.append(k.get("input"))
            return super().create(**k)

    client = Rec(script)
    agent.run("do it", _ctx(), client)
    assert "DIFFERENT approach" in json.dumps(client.inputs[-1], default=str)


# ── check its own work (#gaps 1 & 4) ──────────────────────────────────────────

class _FakeTeam:
    def __init__(self, reply):
        self._reply = reply
        self.ran = []

    def run(self, task, ctx, client, *, history=None, instructions=None,
            context=""):
        self.ran.append(task)
        return self._reply


def test_verify_repairs_a_real_gap(db, monkeypatch):
    import verify
    monkeypatch.setattr(agent_core, "_client", lambda: object())
    monkeypatch.setattr(verify, "check",
                        lambda *a, **k: {"ok": False, "gap": "the file was not written"})
    ctx = _ctx()
    ctx.evidence["results"].append({"detail": "did a thing"})   # a tool ran
    team = _FakeTeam("now the file is written")
    out = agent_core._verify_and_repair(
        "research the topic and write the summary file", "I researched it.",
        team, ctx, [], "inst", lambda e: None)
    assert team.ran and "file is written" in out               # one repair ran


def test_verify_passes_clean_work_without_repairing(db, monkeypatch):
    import verify
    monkeypatch.setattr(agent_core, "_client", lambda: object())
    monkeypatch.setattr(verify, "check", lambda *a, **k: {"ok": True, "gap": ""})
    ctx = _ctx()
    ctx.evidence["results"].append({"detail": "x"})
    team = _FakeTeam("SHOULD NOT BE USED")
    out = agent_core._verify_and_repair(
        "research X and summarise it", "Here is the summary.",
        team, ctx, [], "i", lambda e: None)
    assert out == "Here is the summary." and not team.ran       # no needless redo


def test_verify_skips_chat_and_trivial(db, monkeypatch):
    import verify
    called = {"n": 0}

    def spy(*a, **k):
        called["n"] += 1
        return {"ok": True, "gap": ""}

    monkeypatch.setattr(verify, "check", spy)
    ctx = _ctx()                                   # no evidence -> no tool ran
    out = agent_core._verify_and_repair("hi there", "Hello!",
                                        _FakeTeam("x"), ctx, [], "i",
                                        lambda e: None)
    assert out == "Hello!" and called["n"] == 0    # a greeting is never checked


def test_run_python_stays_serial(db):
    """run_python is NOT parallelised even if two are requested in one round."""
    agent = agent_core.Agent("o", "m", "low", "i", [agent_core.RUN_PYTHON], 5)
    tools = agent._tools()

    class FC:
        def __init__(self, cid):
            self.name = "run_python"
            self.arguments = '{"code": "print(1)", "why": "t"}'
            self.call_id = cid

    pairs = agent._execute([FC("a"), FC("b")], _ctx(), tools)
    assert len(pairs) == 2 and all(p[1].get("ok") for p in pairs)
