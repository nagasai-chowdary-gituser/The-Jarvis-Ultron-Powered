"""G80: self-heal - Jarvis fixes what it safely can, bounded.

Self-eval measures reliability; self-heal repairs it. This proves the two forms:
a health check that reports honestly what is and is not working, and a bounded
module installer that acquires a missing library (validated name, one attempt,
never shelled) - with run_python retrying the code once after a heal.
"""
import _harness as h

h.fresh_db()
import selfheal

# health_check: correct shape, and honest about what is present vs missing.
hc = selfheal.health_check()
h.need(isinstance(hc, dict) and "checks" in hc and "summary" in hc,
       f"health_check shape is wrong: {hc}")
names = {c["name"]: c["ok"] for c in hc["checks"]}
h.need(names.get("import safety") is True, "a core module did not import")
h.need(names.get("database writable") is True, "the database check failed")
h.need(any("OPENAI_API_KEY" in n for n in names), "no key presence check")

# ensure_module: an already-present module is a no-op success (no install).
r = selfheal.ensure_module("json")
h.need(r["ok"] and r.get("already"), f"a present module was not detected: {r}")

# ensure_module: an unsafe name is REFUSED and never reaches a shell.
for bad in ("foo; rm -rf ~", "../evil", "a b c", "pkg && curl x"):
    res = selfheal.ensure_module(bad)
    h.need(not res["ok"], f"an unsafe module name was accepted: {bad!r} -> {res}")

# missing_module: pulls the TOP-LEVEL package out of a traceback.
h.need(selfheal.missing_module(
    "ModuleNotFoundError: No module named 'pandas'") == "pandas", "parse failed")
h.need(selfheal.missing_module(
    "No module named 'pandas.core'") == "pandas", "did not take the top package")
h.need(selfheal.missing_module("some unrelated error") is None,
       "missing_module invented a package")

# run_python's heal path does not disturb code that was fine all along.
import brain
ok = brain.run_python("import json; print(json.dumps({'ok': True}))")
h.need(ok.get("ok") and "ok" in ok.get("stdout", ""),
       f"a normal run_python broke under the heal wrapper: {ok}")

h.ok("SELFHEAL_OK")
