"""
The self-check: verify.check confirms a goal is really met, flags a concrete
gap when it is not, and - the load-bearing property - NEVER blocks a reply when
the checker itself fails. The model is mocked; no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import state      # noqa: E402
import verify     # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(state, "_ready", set(), raising=False)
    return tmp_path


def _fake_model(monkeypatch, output_text):
    class FakeResp:
        pass
    FakeResp.output_text = output_text
    FakeResp.usage = None

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = self

        def create(self, **k):
            return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)


def test_met_goal_passes(db, monkeypatch):
    _fake_model(monkeypatch, '{"met": true, "gap": ""}')
    out = verify.check("message Kap hi", "Sent 'hi' to Kap.",
                       {"results": [{"detail": "sent"}]})
    assert out["ok"] is True and out["gap"] == ""


def test_unmet_goal_returns_the_gap(db, monkeypatch):
    _fake_model(monkeypatch,
                '{"met": false, "gap": "the summary was never written"}')
    out = verify.check("research X and write a summary",
                       "I researched X.",
                       {"results": [{"detail": "read 3 pages"}]})
    assert out["ok"] is False
    assert "summary" in out["gap"]


def test_broken_checker_never_blocks_a_reply(db, monkeypatch):
    # The model raises -> the work must stand (ok=True), not be swallowed.
    class Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("model down")

    import openai
    monkeypatch.setattr(openai, "OpenAI", Boom)
    out = verify.check("do the thing", "Done.", {"results": [{"detail": "x"}]})
    assert out["ok"] is True and out["gap"] == ""


def test_garbage_json_is_treated_as_met(db, monkeypatch):
    _fake_model(monkeypatch, "sorry, I could not produce JSON")
    out = verify.check("do the thing", "Done.", None)
    assert out["ok"] is True          # unsure -> never a false "not done"


def test_empty_goal_or_reply_skips(db):
    assert verify.check("", "something")["ok"] is True
    assert verify.check("a goal", "")["ok"] is True
