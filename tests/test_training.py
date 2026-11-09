"""
The learn-by-doing engine: a verified operating success becomes a reusable
PROCEDURE, is recalled for a similar request, reinforced when it works again, and
distrusted when it fails. All offline (the distiller model is faked).
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import state  # noqa: E402
import training  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(state, "_ready", set(), raising=False)
    return tmp_path


# ── the store ─────────────────────────────────────────────────────────────────

def test_a_procedure_is_saved_and_reinforced_not_duplicated(db):
    state.save_procedure("make-ppt-chatgpt", "Make a PPT using ChatGPT",
                         "ppt chatgpt deck", ["open", "type", "read"], "file saved")
    rows = state.procedures()
    assert len(rows) == 1 and rows[0]["uses"] == 1
    assert rows[0]["steps"] == ["open", "type", "read"]

    # the SAME skill done again: reinforced (use-count up), latest steps kept
    state.save_procedure("make-ppt-chatgpt", "Make a PPT using ChatGPT",
                         "ppt chatgpt deck", ["open", "type", "read", "save"], "x")
    rows = state.procedures()
    assert len(rows) == 1 and rows[0]["uses"] == 2
    assert rows[0]["steps"][-1] == "save"          # gets better, not duplicated


# ── what is worth learning ────────────────────────────────────────────────────

def test_only_a_multistep_operating_run_is_worth_learning():
    assert not training.worth_learning("ok answer_question")        # one read
    assert not training.worth_learning(
        "ok recall\nok answer_question\nok recall")                 # reads only
    assert training.worth_learning(
        "ok open_chrome_profile\nok set_control\nok read_text")     # it DID things


# ── recall ────────────────────────────────────────────────────────────────────

def test_match_finds_the_right_procedure_and_ignores_the_unrelated(db):
    state.save_procedure("make-ppt-chatgpt", "Make a PPT using ChatGPT",
                         "ppt chatgpt deck slides", ["a", "b", "c"], "")
    m = training.match("make a ppt in chatgpt about mars")
    assert m and m["title"] == "Make a PPT using ChatGPT"
    assert training.match("what is my cpu usage") is None


def test_a_flaky_procedure_is_not_reused(db):
    state.save_procedure("x", "Do the thing", "flaky thing task",
                         ["a", "b", "c"], "")
    state.procedure_failed("x")
    state.procedure_failed("x")                    # fails(2) > uses(1)
    assert training.match("do the flaky thing task now") is None


def test_render_shows_the_proven_steps(db):
    state.save_procedure("x", "Make a PPT using ChatGPT", "ppt chatgpt",
                         ["open one tab", "type prompt", "read answer"],
                         "deck saved")
    block = training.block("make a ppt in chatgpt")
    assert "PROVEN PROCEDURE" in block and "Make a PPT using ChatGPT" in block
    assert "open one tab" in block and "deck saved" in block


def test_no_match_injects_nothing(db):
    assert training.block("just say hi") == ""


# ── learn by doing ────────────────────────────────────────────────────────────

def test_learn_distills_a_procedure_from_a_successful_trace(db, monkeypatch):
    monkeypatch.setattr(training.models, "record", lambda *a, **k: None)

    class FakeResp:
        output_text = (
            '{"title":"Make a PPT using ChatGPT","trigger":"ppt chatgpt deck",'
            '"steps":["open_chrome_profile main chatgpt.com one tab",'
            '"set_control the full prompt","read_text the answer",'
            '"make_pptx from it"],"done_check":"the .pptx is saved"}')

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = self

        def create(self, **k):
            return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)

    trace = ("ok open_chrome_profile\nok read_window\nok set_control\n"
             "ok read_text\nok make_pptx")
    training.learn("make a ppt in chatgpt in my main account", "done", trace)
    for t in threading.enumerate():
        if t.name == "training-learn":
            t.join(timeout=5)

    m = training.match("make a ppt in chatgpt about the floods")
    assert m and "ChatGPT" in m["title"]           # learned once, recalled after


def test_learn_skips_a_trivial_or_read_only_turn(db, monkeypatch):
    called = []
    monkeypatch.setattr(training, "_slug", lambda s: called.append(s) or "x")
    training.learn("what is my cpu", "50%", "ok stats")   # one read, not worth it
    assert state.procedures() == [] and called == []
