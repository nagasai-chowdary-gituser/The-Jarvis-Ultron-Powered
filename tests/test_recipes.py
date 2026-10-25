"""
Skill crystallization (#gap 5): a working solution can be saved as a named
recipe and run again; promotion is gated (deletion and run_python-bypass forms
refused); recipes are DATA, parsed not imported, so a file never executes on
load. brain.run_python is faked - no subprocess, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import recipes  # noqa: E402


@pytest.fixture()
def tools_dir(tmp_path, monkeypatch):
    d = tmp_path / "tools"
    monkeypatch.setattr(recipes, "TOOLS_DIR", d)
    return d


def test_save_and_list_a_skill(tools_dir):
    out = recipes.save_skill("count_downloads",
                             "Count files in the Downloads folder",
                             "import os; print(len(os.listdir('.')))")
    assert out["ok"] and (tools_dir / "count_downloads.py").exists()
    names = [s["name"] for s in recipes.list_skills()]
    assert "count_downloads" in names


def test_saved_skill_is_parsed_not_imported(tools_dir):
    # A recipe file must be readable as data even if importing it would blow up.
    recipes.save_skill("greet", "print a greeting", "print('hi')")
    recs = recipes.all_skills()
    assert len(recs) == 1
    assert recs[0]["schema"]["name"] == "greet"
    assert "print('hi')" in recs[0]["code"]


def test_deletion_is_refused_at_promotion(tools_dir):
    out = recipes.save_skill("nuke", "delete a file",
                             "import os; os.remove('x')")
    assert out["ok"] is False and "refus" in out["error"].lower()
    assert not (tools_dir / "nuke.py").exists()


def test_run_python_bypass_forms_are_rejected(tools_dir):
    # subprocess trips the recipe "bypass" guard; exec/__import__ are refused
    # even harder, by the runtime-built-code preflight, before it. Either way
    # the skill is not saved.
    for code in ("import subprocess; subprocess.run(['x'])",
                 "exec('print(1)')",
                 "__import__('os').getcwd()"):
        out = recipes.save_skill("sneaky", "runs some code", code)
        err = out["error"].lower()
        assert out["ok"] is False and ("bypass" in err or "refus" in err)
        assert not (tools_dir / "sneaky.py").exists()


def test_override_allows_a_forbidden_form_on_purpose(tools_dir):
    out = recipes.save_skill("shellit", "runs a shell thing",
                             "import subprocess; print('x')",
                             override_forbidden=True)
    assert out["ok"] is True


def test_bad_names_rejected(tools_dir):
    assert recipes.save_skill("X", "does a thing", "print(1)")["ok"] is False   # upper
    assert recipes.save_skill("a b", "does a thing", "print(1)")["ok"] is False # space
    assert recipes.save_skill("ok_name", "does a thing", "print(1)")["ok"]      # fine


def test_phone_safe_defaults_off(tools_dir):
    recipes.save_skill("x_skill", "does x", "print(1)")
    assert recipes.list_skills()[0]["phone_safe"] is False
    recipes.save_skill("y_skill", "does y", "print(1)", phone_safe=True)
    ys = [s for s in recipes.list_skills() if s["name"] == "y_skill"][0]
    assert ys["phone_safe"] is True


def test_run_skill_injects_params_and_runs_via_run_python(tools_dir, monkeypatch):
    seen = {}
    monkeypatch.setattr(recipes.brain, "run_python",
                        lambda code, why: seen.update(code=code, why=why)
                        or {"ok": True, "stdout": "42"})
    recipes.save_skill(
        "echo_n", "print the given number",
        "print(n)",
        params={"type": "object", "properties": {"n": {"type": "integer"}},
                "required": ["n"]})
    rec = recipes.all_skills()[0]
    out = recipes.run_skill(rec, {"n": 42})
    assert out["ok"] and out["stdout"] == "42"
    assert "n = 42" in seen["code"] and "print(n)" in seen["code"]  # param injected
    assert seen["why"].startswith("recipe:")
