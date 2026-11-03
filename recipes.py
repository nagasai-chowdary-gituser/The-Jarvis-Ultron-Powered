"""
recipes.py — earned skills. Save what worked once, call it by name forever after.

THE GAP THIS FILLS. AGENTS.md §7 specified recipes - when run_python solves
something and it works, promote it to tools/ so next time it is one call instead
of regenerating code (3x cheaper, and more reliable because it is known-good).
But nothing ever created them: tools/ stayed empty, so Jarvis started every
repeated task from zero and was as slow and error-prone the fiftieth time as the
first. This is how it LEARNS to do things, next to how memory learns the person.

    recipes.list()                 -> [{name, description, phone_safe}]
    recipes.find(query)            -> the recipes whose description matches (§7.3)
    recipes.run(name, **kwargs)    -> call one
    recipes.index()                -> a compact block for the prompt (cached)
    recipes.save(name, description, code, phone_safe=False) -> promote code

PROMOTION IS A PRIVILEGE BOUNDARY (§7.1). A recipe is arbitrary Python given a
name; promoting it does not sanitise it, and a promoted recipe that shells out is
a phone-reachable run_python bypass - exactly what the safety boundary exists to
prevent. So save() REFUSES any recipe containing eval/exec/subprocess/__import__/
importlib unless force=True, and phone_safe defaults to False: a recipe is opened
to the phone deliberately, never by accident.

Auto-discovery (§7): any module in tools/ exporting SCHEMA + run() registers on
import. Dropping a file in is the whole install.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).parent
TOOLS_DIR = ROOT / "tools"

# Names a promoted recipe must not contain - each is a way back to arbitrary code
# execution from a phone-reachable surface. Word-boundaried so "subprocessing" and
# "evaluate" are not false positives.
_REJECT = ("eval", "exec", "subprocess", "__import__", "importlib")

_index_cache: dict = {"sig": None, "val": ""}


def _dir_sig():
    if not TOOLS_DIR.exists():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime)
                        for p in TOOLS_DIR.glob("*.py") if not p.name.startswith("_")))


def _modules():
    """Import every tools/*.py that exports SCHEMA + run(). A broken recipe is
    skipped, never fatal - one bad file must not take out the whole vocabulary."""
    out = []
    if not TOOLS_DIR.exists():
        return out
    for p in sorted(TOOLS_DIR.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"recipe_{p.stem}", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "SCHEMA") and hasattr(mod, "run") and callable(mod.run):
                out.append((p.stem, mod))
        except Exception:
            continue
    return out


def list() -> list[dict]:
    rows = []
    for name, mod in _modules():
        schema = getattr(mod, "SCHEMA", {}) or {}
        rows.append({"name": schema.get("name", name),
                     "description": schema.get("description", ""),
                     "phone_safe": bool(schema.get("phone_safe", False)),
                     "telegram_safe": bool(schema.get("telegram_safe", False))})
    return rows


def find(query: str, limit: int = 3) -> list[dict]:
    """Semantic-ish lookup by description (§7.3): 'check my project' and 'repo
    status' should reach the same recipe, so match on words, not the exact name."""
    q = {w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 2}
    if not q:
        return []
    scored = []
    for r in list():
        words = set(re.findall(r"[a-z0-9]+",
                               f"{r['name']} {r['description']}".lower()))
        overlap = len(q & words)
        if overlap:
            scored.append((overlap, r))
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored[:limit]]


def run(name: str, **kwargs) -> dict:
    for n, mod in _modules():
        schema = getattr(mod, "SCHEMA", {}) or {}
        if name in (n, schema.get("name")):
            try:
                return mod.run(**kwargs)
            except Exception as e:
                return {"ok": False, "error": f"recipe {name!r} failed: "
                                              f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": f"no recipe named {name!r}",
            "have": [r["name"] for r in list()]}


def index() -> str:
    """A compact list of saved recipes for the prompt tail. Cached on the tools/
    directory signature so it is not rebuilt (re-importing modules) every request;
    empty string when nothing is saved, so it costs nothing until a recipe exists."""
    sig = _dir_sig()
    if _index_cache["sig"] != sig:
        rows = list()
        if rows:
            lines = ["", "YOUR SAVED RECIPES (one call each - prefer these over "
                     "regenerating code; run: import recipes; recipes.run('name', ...))"]
            lines += [f"- {r['name']}: {r['description']}" for r in rows]
            _index_cache["val"] = "\n".join(lines) + "\n"
        else:
            _index_cache["val"] = ""
        _index_cache["sig"] = sig
    return _index_cache["val"]


def save(name: str, description: str, code: str, phone_safe: bool = False,
         telegram_safe: bool = False, force: bool = False) -> dict:
    """Promote working code into tools/<name>.py. The code must already be
    refactored into run(**kwargs) (the model does that second pass, §7.2); this
    writes it, attaches SCHEMA, and enforces the §7.1 rejection."""
    name = re.sub(r"[^a-z0-9_]", "", (name or "").lower().strip())
    if not name:
        return {"ok": False, "error": "a recipe needs a simple name"}
    if "def run(" not in (code or ""):
        return {"ok": False, "error": "the recipe code must define run(**kwargs)"}
    hits = [w for w in _REJECT if re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])",
                                            code)]
    if hits and not force:
        return {"ok": False, "refused": True,
                "error": f"a promoted recipe must not contain {hits}: promoted, it "
                         f"becomes callable from the phone, and that would be a "
                         f"run_python bypass (§7.1). Rewrite without them.",
                "hint": "do the work with the lib/ functions instead of shelling out"}
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    schema = {"name": name, "description": (description or "").strip()[:200],
              "phone_safe": bool(phone_safe), "telegram_safe": bool(telegram_safe)}
    body = (f'"""Recipe: {name} - promoted from a real execution."""\n'
            f"SCHEMA = {schema!r}\n\n\n{code.strip()}\n")
    path = TOOLS_DIR / f"{name}.py"
    path.write_text(body, encoding="utf-8")
    _index_cache["sig"] = None            # force the prompt index to rebuild
    return {"ok": True, "name": name, "path": str(path),
            "note": f"saved; next time call recipes.run('{name}', ...). "
                    f"phone_safe={schema['phone_safe']}"}
