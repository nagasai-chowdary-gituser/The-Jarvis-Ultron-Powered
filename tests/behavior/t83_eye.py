"""G83: the eye opens while Jarvis looks at the screen and closes the moment it stops.

A privacy tell the owner can see. This proves the cross-process signal (open on
look_start, closed on look_stop), the staleness backstop (a crashed capture cannot
leave the eye stuck open), that vision.see_screen brackets it on EVERY exit path,
and that the overlay's GUI is lazy so the module loads with no display.
"""
import inspect
import time

import _harness as h

h.fresh_db()
import eye

# 1. The open/closed signal.
eye.look_start()
h.need(eye.is_looking() is True, "the eye did not open on look_start")
eye.look_stop()
h.need(eye.is_looking() is False, "the eye did not close on look_stop")

# 2. A crashed capture (a stale 'open') auto-closes via the freshness backstop.
eye.EYE_FILE.write_text(f"open {time.time() - eye._FRESH_S - 5:.3f}",
                        encoding="utf-8")
h.need(eye.is_looking() is False, "a stale 'open' left the eye stuck open")

# 3. The quit signal (Jarvis shutdown) reads as closed and asks the overlay to end.
eye.look_quit()
h.need(eye._should_quit() is True, "the quit signal was not detected")
h.need(eye.is_looking() is False, "'quit' must not read as looking")

# 4. vision.see_screen brackets the eye: it fails here (no display/key), but the
#    finally must still close it - the eye may never be left open after a read.
eye.look_stop()
from lib import vision
r = vision.see_screen("what is on screen?")
h.need(isinstance(r, dict), "see_screen did not return a dict")
h.need(eye.is_looking() is False, "see_screen left the eye OPEN after finishing")
h.need(eye.EYE_FILE.exists(), "see_screen never touched the eye flag")

# 5. The GUI is lazy, so importing eye needs no display (tkinter inside run()).
src = inspect.getsource(eye)
h.need("def run" in src and "import tkinter" in src, "the overlay run() is missing")
h.need(src.index("import tkinter") > src.index("def run"),
       "tkinter must be imported inside run(), not at module top")
h.need("-topmost" in src and "overrideredirect" in src,
       "the overlay is not a borderless always-on-top window")

h.ok("EYE_OK")
