"""
eye.py — a little eye at the top of the screen that OPENS when Jarvis is looking.

Privacy you can see. A screenshot carries whatever is on screen off the machine
(§6.1), so when Jarvis uses its eyes the owner should KNOW, the moment it happens,
on any window and any desktop - not find out from a log later. This is that tell:
a small eye, always on top, centred at the top edge. It is OPEN while
vision.see_screen is reading the screen and CLOSED the rest of the time.

HOW THE TWO PROCESSES AGREE. vision.see_screen runs inside a run_python
subprocess; this overlay is its own process. They cannot share memory, so they
share a tiny flag FILE under the workspace: see_screen writes "open <time>" when
it starts and "shut <time>" when it finishes, and this overlay polls it. The time
is a safety net - a capture that crashes without writing "shut" still closes the
eye after _FRESH_S, so a dead process can never leave the eye stuck open.

WINDOWS AT RUNTIME, importable anywhere. tkinter and the Win32 click-through are
imported lazily inside run(), so the module (and its signal functions, which
vision.py calls) load on any OS with no display. The overlay itself needs a
screen, which is the machine Jarvis runs on.

    python eye.py          # show the eye (jarvis.bat starts it for you)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import safety

# The flag file both processes use. Under the workspace, so vision (in a
# subprocess) and this overlay resolve the SAME path from the same JARVIS_WORKSPACE.
EYE_FILE = safety.WORKSPACE / ".jarvis_eye"

# A crashed capture must not leave the eye open forever. If the last "open" mark
# is older than this, the eye closes regardless. A single see_screen is seconds;
# the run_python timeout is 60s, so this is a generous backstop, not a limit.
_FRESH_S = 25.0

W = H = 46                      # the overlay is small and unobtrusive


# ─────────────────────────────────────────────────────────────────────────────
# The signal — no GUI, safe to import and call from vision.py
# ─────────────────────────────────────────────────────────────────────────────

def _write(state: str) -> None:
    try:
        safety.WORKSPACE.mkdir(parents=True, exist_ok=True)
        EYE_FILE.write_text(f"{state} {time.time():.3f}", encoding="utf-8")
    except Exception:
        pass            # the eye is a nicety; it must never break a capture


def look_start() -> None:
    """Jarvis has begun reading the screen - open the eye."""
    _write("open")


def look_stop() -> None:
    """Jarvis has finished - close the eye."""
    _write("shut")


def look_quit() -> None:
    """Ask a running overlay to exit (e.g. on Jarvis shutdown)."""
    _write("quit")


def _read() -> tuple[str, float]:
    try:
        parts = EYE_FILE.read_text(encoding="utf-8").split()
        return parts[0], float(parts[1])
    except Exception:
        return "shut", 0.0


def is_looking() -> bool:
    """True only while a FRESH 'open' mark stands - the eye's open/closed state."""
    state, ts = _read()
    return state == "open" and (time.time() - ts) < _FRESH_S


def _should_quit() -> bool:
    return _read()[0] == "quit"


# ─────────────────────────────────────────────────────────────────────────────
# The overlay — Windows at runtime, lazy imports
# ─────────────────────────────────────────────────────────────────────────────

def _click_through(root) -> None:
    """Let the mouse pass straight through the eye, so an overlay sitting at the
    top of every window never eats a click. Win32 only; a no-op elsewhere. Also
    WS_EX_TOOLWINDOW, so it stays out of Alt-Tab and the taskbar."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
        WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x00000020, 0x00000080
        cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            cur | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def _draw(canvas, tk, transparent: str, looking: bool) -> None:
    canvas.delete("all")
    cx, cy = W // 2, H // 2
    if looking:
        # an open eye: almond outline, dark fill, green iris, pupil, a glint.
        canvas.create_oval(5, cy - 10, W - 5, cy + 10,
                           fill="#06140a", outline="#39ff14", width=2)
        canvas.create_oval(cx - 8, cy - 8, cx + 8, cy + 8,
                           fill="#1f8a1f", outline="#2bd12b")
        canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#000000")
        canvas.create_oval(cx - 1, cy - 6, cx + 2, cy - 3, fill="#eaffea")
    else:
        # a closed eye: a calm, dim lid. Always visible, so the owner knows the
        # tell is there even when Jarvis is not looking.
        canvas.create_arc(5, cy - 10, W - 5, cy + 12,
                          start=200, extent=140, style="arc",
                          outline="#5a5a5a", width=2)


def run() -> int:
    import tkinter as tk

    root = tk.Tk()
    root.title("jarvis-eye")
    root.overrideredirect(True)                 # no title bar, no border
    root.attributes("-topmost", True)
    transparent = "#010203"
    try:
        root.attributes("-transparentcolor", transparent)  # Windows: box vanishes
    except Exception:
        transparent = ""
    bg = transparent or "#101014"
    root.configure(bg=bg)

    sw = root.winfo_screenwidth()
    root.geometry(f"{W}x{H}+{(sw - W) // 2}+0")   # top-middle

    canvas = tk.Canvas(root, width=W, height=H, bg=bg, highlightthickness=0,
                       bd=0)
    canvas.pack()
    root.update_idletasks()
    _click_through(root)
    look_stop()                 # always start closed, never a stale "open"

    def tick():
        if _should_quit():
            root.destroy()
            return
        try:
            _draw(canvas, tk, bg, is_looking())
            root.attributes("-topmost", True)    # stay above fullscreen flips
        except Exception:
            pass
        root.after(160, tick)

    tick()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        look_start()
        print("looking:", is_looking())
        look_stop()
        print("looking:", is_looking())
    else:
        raise SystemExit(run())
