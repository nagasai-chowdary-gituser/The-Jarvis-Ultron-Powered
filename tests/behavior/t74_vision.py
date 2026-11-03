"""G74: the eyes are present, and they fail SAFELY.

see_screen / list_windows restore the screen-vision loop. They must import on any
OS and, when they cannot see (no display) or cannot read (no key), return a dict
the model can act on - never raise into the turn, exactly like run_python.
"""
import os

import _harness as h

h.fresh_db()
from lib import vision

# list_windows always returns a dict with an ok flag, on any OS.
lw = vision.list_windows()
h.need(isinstance(lw, dict) and "ok" in lw, f"list_windows wrong shape: {lw}")

# see_screen without a key (or without a display) fails to a dict, never raises.
saved = os.environ.pop("OPENAI_API_KEY", None)
try:
    r = vision.see_screen("what is on the screen?")
    h.need(isinstance(r, dict), f"see_screen did not return a dict: {r!r}")
    h.need(r.get("ok") is False,
           "see_screen claimed success with no key and no display")
    h.need(r.get("error"), "a failed see_screen gave no reason to act on")
finally:
    if saved is not None:
        os.environ["OPENAI_API_KEY"] = saved

# The capture helper names which path it used, so a fallback is never passed off
# as the window that was asked for (§6.1).
import inspect
src = inspect.getsource(vision)
h.need("PrintWindow" in src, "window capture does not use PrintWindow")
h.need("source" in src, "the capture path is not reported back")

h.ok("VISION_OK")
