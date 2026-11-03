"""G70: the delete-guard is REAL. run_python refuses every deletion route.

For a long time the owner's first rule - "Jarvis does not delete my files" -
was only written down. run_python's one content check was money_check(), and a
generated shutil.rmtree ran with his full rights. This proves the AST preflight
now refuses deletion, dynamic code execution and credential reads BEFORE the
subprocess is ever spawned, while ordinary work stays clean and friction-free.
"""
import _harness as h

h.fresh_db()
import brain
import safety

V = safety.Verdict

# Every destructive route the contract (§4.3a) names must be REFUSED outright.
refused = [
    'import os; os.remove("x")',
    'import os; os.unlink("x")',
    'import os; os.rmdir("x")',
    'import shutil; shutil.rmtree("x")',
    'from pathlib import Path; Path("x").unlink()',
    'from shutil import rmtree as nuke; nuke("x")',
    'import os; getattr(os, "remove")("x")',
    'import os; os.system("del x")',
    'import os; os.system("rmdir /s x")',
    'import subprocess; subprocess.run(["cmd","/c","del","x"])',
    'import subprocess; subprocess.run(["powershell","Remove-Item","x"])',
    'import os; os.system("rm -rf x")',
    '__import__("os").remove("x")',
    'exec("import os; os.remove(y)")',
    'from subprocess import run; run(["cmd","/c","del","x"])',
    'open("/home/me/.ssh/id_rsa").read()',
]
for code in refused:
    pf = safety.preflight(code)
    h.need(pf.verdict is V.REFUSED,
           f"preflight let a delete route through: {code!r} -> {pf.verdict}")

# And the refusal must actually stop the subprocess - not merely label it. If a
# file written first is still there afterwards, nothing destructive ran.
ws = h.workspace()
victim = ws / "do-not-delete.txt"
victim.write_text("alive", encoding="utf-8")
out = brain.run_python(f'import os; os.remove(r"{victim}")')
h.need(out.get("refused") is True, f"run_python did not refuse deletion: {out}")
h.need("stdout" not in out, "refused code produced output, so it executed")
h.need(victim.exists(), "THE FILE WAS DELETED - the guard did not hold")
h.need("move" in (out.get("suggestion") or "").lower(),
       "the refusal offers no reversible alternative")

# Ordinary work is untouched: no prompt, no friction, it just runs.
clean = [
    'print("hello")',
    'x=[1,2,3]; x.remove(2); print(x)',          # list.remove is innocent
    'import os; os.rename("a","b")',               # a move is reversible
]
for code in clean:
    pf = safety.preflight(code)
    h.need(pf.verdict is V.CLEAN,
           f"preflight added friction to ordinary code: {code!r} -> {pf.reasons}")

go = brain.run_python('print(2 + 2)')
h.need(go.get("ok") and go.get("stdout", "").strip() == "4",
       f"a clean run_python did not execute: {go}")

# The guard is AST-based, so the literal destructive call never appears in the
# source of safety.py (t29 asserts the same for os-dot-remove-with-paren).
import inspect
src = inspect.getsource(safety.preflight)
h.need("ast" in src.lower(), "preflight is not an AST scan")

h.ok("PREFLIGHT_OK")
