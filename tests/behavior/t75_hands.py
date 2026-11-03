"""G75: the hands are present, prefer NAMES over pixels, and fail safely.

vision gave Jarvis eyes; hands let it touch. The capability is Windows-only
(UI Automation), so here the test proves the contract that holds everywhere: the
functions import on any OS, return an actionable dict rather than raising, and the
primary path is a named control (pywinauto best_match), with coordinates only as
the documented fallback.
"""
import inspect

import _harness as h

h.fresh_db()
from lib import hands

# Every entry point returns a dict and never raises, on any OS.
for call in (lambda: hands.list_controls("Notepad"),
             lambda: hands.click("Save", window="Notepad"),
             lambda: hands.focus("Notepad"),
             lambda: hands.type_text("hello", into="Edit", window="Notepad")):
    r = call()
    h.need(isinstance(r, dict) and "ok" in r,
           f"a hands call did not return an actionable dict: {r!r}")

# Reliability-first: the design leans on UI Automation names, not pixels.
src = inspect.getsource(hands)
h.need("pywinauto" in src, "hands do not use UI Automation at all")
h.need("best_match" in src, "hands do not match controls by name")
h.need("click_at" in src and "fallback" in src.lower(),
       "the coordinate path is not clearly the fallback")

# Special characters are escaped so a literal path/password types as itself.
esc = hands._escape("a+b(c)")
h.need("+" not in esc.replace("{+}", "") and "{+}" in esc,
       f"type_text would misread shortcut characters: {esc!r}")

# off Windows, the refusal names the reason - the model can act on it.
if not hands._IS_WINDOWS:
    r = hands.click("Save")
    h.need(r["ok"] is False and "Windows" in (r.get("error") or ""),
           f"the off-Windows refusal is not actionable: {r}")

h.ok("HANDS_OK")
