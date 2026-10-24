"""
Offline tests for the intelligent memory. Embeddings are faked (deterministic),
so the store, the cosine ranking, dedup, and the keyword fallback are all proven
without a network call.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import memory  # noqa: E402
import state  # noqa: E402


def _fake_embed(texts):
    """Deterministic vectors: shared words -> higher cosine. No network."""
    out = []
    for t in texts:
        v = np.zeros(memory.EMBED_DIM, dtype=np.float32)
        for w in (t or "").lower().split():
            h = abs(hash(w)) % memory.EMBED_DIM
            v[h] += 1.0
        out.append(v if v.any() else np.ones(memory.EMBED_DIM, dtype=np.float32))
    return out


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(memory, "_ready", False, raising=False)
    monkeypatch.setattr(memory, "_cache", None, raising=False)
    monkeypatch.setattr(memory, "embed", _fake_embed)
    return tmp_path


def test_recall_ranks_by_shared_meaning(db):
    memory.remember("the budget is approved by Priya")
    memory.remember("lunch options near the office")
    memory.remember("Priya signs off on spending and budgets")
    hits = memory.recall("who approves the budget spending", k=2)
    assert hits
    assert "Priya" in hits[0]["text"]          # the budget memory ranks first


def test_unrelated_query_returns_little(db):
    memory.remember("the budget is approved by Priya")
    hits = memory.recall("photosynthesis in plants", k=3)
    assert all("Priya" not in h["text"] for h in hits) or not hits


def test_near_identical_is_deduped(db):
    a = memory.remember("Boss prefers one-line replies")
    b = memory.remember("Boss prefers one-line replies")
    assert a == b and memory.count() == 1


def test_keyword_fallback_when_embeddings_down(db, monkeypatch):
    memory.remember("Kap Cut is on WhatsApp as plus 91 6281")
    monkeypatch.setattr(memory, "embed", lambda texts: None)   # embeddings fail
    hits = memory.recall("Kap Cut", k=3)
    assert hits and "Kap Cut" in hits[0]["text"]


def test_recall_block_is_prompt_ready(db):
    # (The fake embed matches on shared words; real embeddings match by meaning,
    # verified live: "where do I study" -> this fact at cosine 0.30.)
    memory.remember("Boss college is Saveetha")
    block = memory.recall_block("what is Boss college")
    assert "Saveetha" in block and "REMEMBER" in block


def test_short_text_is_not_stored(db):
    assert memory.remember("hi") is None
    assert memory.count() == 0
