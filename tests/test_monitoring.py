"""
Jobs can be deleted, and a watched chat alerts the owner about urgent messages
(money, calls, decisions) while staying quiet about chit-chat. All offline:
WhatsApp and the model are mocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import state  # noqa: E402
import worker  # noqa: E402


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(state, "_ready", set(), raising=False)
    return tmp_path


# ── jobs delete (bug 1) ───────────────────────────────────────────────────────

def test_delete_one_job(db):
    a = state.add_job("reminder", {"text": "x"})
    state.add_job("reminder", {"text": "y"})
    assert len(state.jobs()) == 2
    assert state.delete_job(a) is True
    assert len(state.jobs()) == 1
    assert state.delete_job(999999) is False       # nothing to delete


def test_delete_all_jobs(db):
    state.add_job("reminder", {"text": "x"})
    state.add_job("watch_chat", {"contact": "Kapcut"})
    assert state.delete_all_jobs() == 2
    assert state.jobs() == []


def test_watch_poll_is_fast(db):
    assert worker.WATCH_INTERVAL_S <= 6
    assert worker.TICK_S <= 4


# ── urgent detection (bug 2b) ─────────────────────────────────────────────────

def _fake_whatsapp(monkeypatch, incoming, sent):
    from lib import whatsapp
    monkeypatch.setattr(whatsapp, "open", lambda c: {"contact": c})
    monkeypatch.setattr(whatsapp, "new_since",
                        lambda jid, since: [{"text": incoming, "ts": 9e9}])
    monkeypatch.setattr(whatsapp, "send",
                        lambda jid, txt: sent.append(txt) or {"contact": "ok"})


def test_urgent_money_message_alerts_the_boss(db, monkeypatch):
    sent, calls = [], []
    _fake_whatsapp(monkeypatch, "boss you took my 10000, i want it right now", sent)
    monkeypatch.setattr(worker, "_reply_and_judge",
                        lambda *a: ("sure, I'll tell him",
                                    "Kapcut says you took his 10,000 and wants it now"))
    monkeypatch.setattr(worker, "call_boss",
                        lambda reason, **k: calls.append(reason))
    worker.watch_chat({"id": 1, "params": {"contact": "Kapcut"},
                       "created_at": 0, "until": ""})
    assert calls, "the Boss was NOT alerted about an urgent money message"
    assert "10,000" in calls[0]
    assert sent, "no reply was sent on the Boss's behalf"


def test_chitchat_does_not_alert(db, monkeypatch):
    sent, calls = [], []
    _fake_whatsapp(monkeypatch, "hey how are you doing", sent)
    monkeypatch.setattr(worker, "_reply_and_judge",
                        lambda *a: ("good, you?", ""))
    monkeypatch.setattr(worker, "call_boss",
                        lambda reason, **k: calls.append(reason))
    worker.watch_chat({"id": 1, "params": {"contact": "X"},
                       "created_at": 0, "until": ""})
    assert not calls, "chit-chat must NOT interrupt the Boss"


def test_reply_and_judge_parses_the_models_json(db, monkeypatch):
    class FakeResp:
        output_text = ('{"reply":"ok boss","urgent":true,'
                       '"alert":"he wants the money back"}')
        usage = None

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = self

        def create(self, **k):
            return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    reply, alert = worker._reply_and_judge("jid", "Kapcut", "pay me now", "")
    assert reply == "ok boss"
    assert "money" in alert and "Kapcut" in alert


def _fake_openai(monkeypatch, output_text):
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


# ── awareness loop (initiative) ───────────────────────────────────────────────

def test_awareness_alerts_when_something_matters(db, monkeypatch):
    state.add_commitment("call the bank about the overdue payment", due="today")
    _fake_openai(monkeypatch,
                 '{"act":true,"urgent":true,'
                 '"message":"Your payment to the bank is due today."}')
    calls = []
    monkeypatch.setattr(worker, "call_boss", lambda m, **k: calls.append(m))
    monkeypatch.setattr(worker, "tell_boss", lambda m, **k: calls.append(m))
    assert worker.awareness_pulse() is True
    assert calls and "due today" in calls[0]


def test_awareness_stays_quiet_when_nothing(db, monkeypatch):
    state.add_commitment("an old handled thing")
    _fake_openai(monkeypatch, '{"act":false,"urgent":false,"message":""}')
    calls = []
    monkeypatch.setattr(worker, "call_boss", lambda m, **k: calls.append(m))
    monkeypatch.setattr(worker, "tell_boss", lambda m, **k: calls.append(m))
    assert worker.awareness_pulse() is False
    assert not calls, "awareness spoke when nothing mattered"


def test_awareness_skips_model_when_world_is_empty(db, monkeypatch):
    # No commitments, jobs or moved threads -> no spend, no model call.
    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("the model must not be called on an empty world")

    import openai
    monkeypatch.setattr(openai, "OpenAI", Boom)
    assert worker.awareness_pulse() is False


# ── mission: robust long-running jobs (#3) ───────────────────────────────────

def _mission_job(**params):
    return {"id": 1, "params": {"goal": "research X deeply", **params},
            "created_at": 9e9, "until": ""}        # far-future -> never times out


def test_mission_advances_and_checkpoints(db, monkeypatch):
    import agent_core
    monkeypatch.setattr(agent_core, "run_turn",
                        lambda *a, **k: "worked. PROGRESS: found three sources")
    saved = {}
    monkeypatch.setattr(worker, "_save_params", lambda jid, p: saved.update(p))
    out = worker.mission(_mission_job())
    assert out["stop"] is False                    # more to do -> keep going
    assert "found three sources" in saved.get("progress", "")   # checkpointed


def test_mission_done_stops_with_report(db, monkeypatch):
    import agent_core
    monkeypatch.setattr(agent_core, "run_turn",
                        lambda *a, **k: "DONE: here is your full report")
    out = worker.mission(_mission_job())
    assert out["stop"] is True and "report" in out["report"].lower()


def test_mission_blocked_stops_honestly(db, monkeypatch):
    import agent_core
    monkeypatch.setattr(agent_core, "run_turn",
                        lambda *a, **k: "BLOCKED: I need your login for that site")
    out = worker.mission(_mission_job())
    assert out["stop"] is True and "blocked" in out["report"].lower()


def test_mission_recovers_then_gives_up(db, monkeypatch):
    import agent_core

    def boom(*a, **k):
        raise RuntimeError("api down")
    monkeypatch.setattr(agent_core, "run_turn", boom)
    monkeypatch.setattr(worker, "_save_params", lambda jid, p: None)
    # already failed twice; this third failure must stop and report, not spin.
    out = worker.mission(_mission_job(fails=2))
    assert out["stop"] is True and "failed" in out["report"].lower()
    # a single failure keeps going (recovery, not surrender)
    out2 = worker.mission(_mission_job(fails=0))
    assert out2["stop"] is False


def test_reply_and_judge_handles_non_json(db, monkeypatch):
    class FakeResp:
        output_text = "just a plain reply, no json here"
        usage = None

    class FakeClient:
        def __init__(self, *a, **k):
            self.responses = self

        def create(self, **k):
            return FakeResp()

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeClient)
    reply, alert = worker._reply_and_judge("jid", "X", "hello", "")
    assert reply and alert == ""          # degrades to a reply, no false alert
