"""
recipes.py — skill crystallization. The thing that makes Jarvis get FASTER.

THE GAP THIS CLOSES. Every time the owner asked for the same real task, the
orchestrator regenerated the whole run_python from scratch - slower, costlier
and able to fail differently each time. A recipe is a proven solution given a
name: the second time, it is ONE tool call instead of a fresh generation. The
rebuild had specified this (AGENTS.md section 7) but never built it; tools/ did
not even exist.

TWO SAFETY DECISIONS, both deliberate and both stricter than section 7:

1. RECIPES ARE DATA, NOT IMPORTED CODE. Section 7 said "any module in tools/
   exporting SCHEMA + run() registers on import". Importing a module RUNS its
   top level, before any preflight can see it - a skill-shaped hole straight
   past the deletion rule. So a recipe file here holds only two literals,
   SCHEMA and CODE, and is read with ast.literal_eval - NEVER imported, never
   executed at load. The CODE runs only later, and only through
   brain.run_python, so the AST deletion preflight, the subprocess isolation,
   the timeout and the HUD visibility ALL still apply. A recipe has exactly the
   privileges of the run_python that created it, and not one bit more.

2. PROMOTION IS A GATE, NOT A RUBBER STAMP (section 7.1). Saving refuses
   deletion outright (safety.refuse_destructive_code) and rejects eval / exec /
   subprocess / __import__ / importlib / os.system - the run_python-bypass
   forms - unless the owner explicitly overrides. phone_safe defaults to False,
   so promotion never silently widens the phone's reach.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import brain
import safety

ROOT = Path(__file__).resolve().parent
TOOLS_DIR = ROOT / "tools"

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
# The run_python-bypass forms: a recipe that shells out or builds code at
# runtime is an execute_python escape reachable by name, which is exactly what
# the transport/risk gate exists to stop. Rejected unless explicitly overridden.
_FORBIDDEN = re.compile(
    r"\b(eval|exec|compile|__import__|importlib|subprocess|os\.system|"
    r"os\.popen|popen)\b|\.popen\b")


def _ensure_dir() -> None:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def save_skill(name: str, description: str, code: str, *,
               phone_safe: bool = False, params: dict | None = None,
               override_forbidden: bool = False) -> dict:
    """Crystallize a working run_python into tools/<name>.py. Returns
    {ok, path} or {ok:False, error}. Never raises."""
    name = (name or "").strip().lower()
    description = (description or "").strip()
    code = (code or "").strip()
    if not _NAME_RE.match(name):
        return {"ok": False, "error": "name must be lower_snake_case, 3-40 chars"}
    if len(description) < 6:
        return {"ok": False, "error": "give a one-line description of what it does"}
    if len(code) < 4:
        return {"ok": False, "error": "no code to save"}
    try:
        safety.refuse_destructive_code(code)        # deletion is never promotable
    except safety.Blocked as b:
        return {"ok": False, "error": f"refused: {getattr(b, 'spoken', str(b))}"}
    if not override_forbidden and _FORBIDDEN.search(code):
        return {"ok": False,
                "error": "a recipe may not use eval/exec/subprocess/__import__/"
                         "importlib/os.system - that would be an execute_python "
                         "bypass reachable by name. Override only on purpose."}

    schema = {"name": name, "description": description,
              "phone_safe": bool(phone_safe),
              "params": params or {"type": "object", "properties": {},
                                   "required": []}}
    _ensure_dir()
    path = TOOLS_DIR / f"{name}.py"
    body = ("# Crystallized skill - DATA ONLY. Read with ast.literal_eval, never\n"
            "# imported or executed on load. CODE runs only via brain.run_python\n"
            "# (subprocess + deletion preflight + timeout), with the privileges of\n"
            "# the run_python that created it and no more.\n"
            f"SCHEMA = {schema!r}\n\n"
            "CODE = r'''\n" + code.replace("'''", "' ' '") + "\n'''\n")
    path.write_text(body, encoding="utf-8")
    return {"ok": True, "path": str(path), "name": name}


def _read_skill(path: Path) -> dict | None:
    """Parse a recipe file WITHOUT importing it: pull the SCHEMA and CODE
    literals out of the AST. A malformed or booby-trapped file is skipped, never
    run."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    schema = code = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            value = ast.literal_eval(node.value)
        except Exception:
            continue
        if target.id == "SCHEMA" and isinstance(value, dict):
            schema = value
        elif target.id == "CODE" and isinstance(value, str):
            code = value
    if not schema or code is None:
        return None
    schema.setdefault("name", path.stem)
    return {"schema": schema, "code": code, "path": str(path)}


def all_skills() -> list[dict]:
    """Every saved recipe, parsed (not imported). Each: {schema, code, path}."""
    if not TOOLS_DIR.is_dir():
        return []
    out = []
    for p in sorted(TOOLS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        rec = _read_skill(p)
        if rec:
            out.append(rec)
    return out


def list_skills(phone_safe_only: bool = False) -> list[dict]:
    """Names + descriptions for recipe_list / the status tools."""
    out = []
    for rec in all_skills():
        s = rec["schema"]
        if phone_safe_only and not s.get("phone_safe"):
            continue
        out.append({"name": s["name"], "description": s.get("description", ""),
                    "phone_safe": bool(s.get("phone_safe"))})
    return out


def _preamble(args: dict, schema: dict) -> str:
    """Turn the call's arguments into safe Python literal assignments, so a
    recipe's CODE can read its parameters by name. repr() of a JSON value is a
    Python literal, so nothing here can inject code."""
    props = ((schema.get("params") or {}).get("properties") or {})
    lines = []
    for key in props:
        if key in (args or {}):
            lines.append(f"{key} = {args[key]!r}")
    return ("\n".join(lines) + "\n") if lines else ""


def run_skill(rec: dict, args: dict) -> dict:
    """Execute one recipe: its parameters as literal assignments, then its CODE,
    all through brain.run_python - the same subprocess, preflight and timeout as
    any generated code."""
    code = _preamble(args, rec["schema"]) + rec["code"]
    name = rec["schema"].get("name", "recipe")
    return brain.run_python(code, f"recipe:{name}")
