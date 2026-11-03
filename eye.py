"""
eye.py — a little eye at the top of the screen that OPENS when Jarvis is looking.

Privacy you can see. A screenshot carries whatever is on screen off the machine
(§6.1), so when Jarvis uses its eyes the owner should KNOW, the moment it happens,
on any window and any desktop - not find out from a log later. This is that tell:
a small eye, always on top, centred at the top edge. It is OPEN (bright green)
while vision.see_screen is reading the screen and CLOSED (a dim teal, sleeping
eye - still clearly visible) the rest of the time.

HOW THE TWO PROCESSES AGREE. vision.see_screen runs inside a run_python
subprocess; this overlay is its own process. They cannot share memory, so they
share a tiny flag FILE under the workspace: see_screen writes "open <time>" when
it starts and "shut <time>" when it finishes, and this overlay polls it. The time
is a safety net - a capture that crashes without writing "shut" still closes the
eye after _FRESH_S, so a dead process can never leave the eye stuck open.

STAYING ON TOP OF THE HUD. The ULTRON HUD is a borderless full-screen window that
also makes itself topmost, so this overlay re-asserts HWND_TOPMOST every tick via
SetWindowPos (not just the tk attribute, which does not re-raise once set). That
is what keeps the eye above the HUD rather than behind it.

WINDOWS AT RUNTIME, importable anywhere. tkinter and the Win32 calls are imported
lazily inside run(), so the module (and the signal functions vision.py calls) load
on any OS with no display. Startup and any crash are written to eye.log, because a
process started with `start` that dies on launch otherwise vanishes with no trace.

    python eye.py          # show the eye (jarvis.bat starts it for you)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import safety

EYE_FILE = safety.WORKSPACE / ".jarvis_eye"
LOG = safety.ROOT / "eye.log"
_FRESH_S = 25.0
W = H = 60                      # a touch bigger, so it is easy to see


def _log(msg: str) -> None:
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# The signal — no GUI, safe to import and call from vision.py
# ─────────────────────────────────────────────────────────────────────────────

def _write(state: str) -> None:
    try:
        safety.WORKSPACE.mkdir(parents=True, exist_ok=True)
        EYE_FILE.write_text(f"{state} {time.time():.3f}", encoding="utf-8")
    except Exception:
        pass


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

def _hwnd(root):
    import ctypes
    wid = root.winfo_id()
    parent = ctypes.windll.user32.GetParent(wid)
    return parent or wid


def _click_through(root) -> None:
    """Let the mouse pass straight through the eye, so an overlay at the top of
    every window never eats a click. Win32 only; a no-op elsewhere. WS_EX_TOOLWINDOW
    also keeps it out of Alt-Tab and the taskbar."""
    try:
        import ctypes
        hwnd = _hwnd(root)
        GWL_EXSTYLE, WS_EX_LAYERED = -20, 0x00080000
        WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x00000020, 0x00000080
        cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            cur | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
    except Exception as e:
        _log(f"click_through failed (not fatal): {e}")


def _raise_topmost(root) -> None:
    """Force the eye to the very top of the z-order WITHOUT stealing focus - this
    is what keeps it above the HUD, which is itself topmost."""
    try:
        import ctypes
        HWND_TOPMOST = -1
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0010
        ctypes.windll.user32.SetWindowPos(
            _hwnd(root), HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
    except Exception:
        pass


def _draw(canvas, looking: bool) -> None:
    canvas.delete("all")
    cx, cy = W // 2, H // 2
    ew, eh = 24, 13                        # eye half-width, half-height
    if looking:
        # OPEN: a bright green eye, iris, pupil, glint, with a soft glow ring.
        canvas.create_oval(cx - ew - 3, cy - eh - 3, cx + ew + 3, cy + eh + 3,
                           outline="#0f3d0f", width=1)
        canvas.create_oval(cx - ew, cy - eh, cx + ew, cy + eh,
                           fill="#06160a", outline="#39ff14", width=3)
        canvas.create_oval(cx - 9, cy - 9, cx + 9, cy + 9,
                           fill="#28c828", outline="#7bff7b", width=1)
        canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="#021002")
        canvas.create_oval(cx - 2, cy - 7, cx + 2, cy - 3, fill="#eaffea")
    else:
        # CLOSED: a calm, SLEEPING eye - clearly visible, so the owner always
        # sees the tell is there, just dimmed and shut.
        canvas.create_line(cx - ew, cy, cx - ew + 4, cy - 4,
                           fill="#1d6f6f", width=2)       # inner corner
        canvas.create_arc(cx - ew, cy - eh, cx + ew, cy + eh + 6,
                          start=200, extent=140, style="arc",
                          outline="#35b5b5", width=3)      # the shut lid
        for dx in (-10, 0, 10):                            # a few lashes
            canvas.create_line(cx + dx, cy + 7, cx + dx, cy + 11,
                               fill="#1d6f6f", width=1)


def run(demo: bool = False) -> int:
    """Show the overlay. demo=True ignores the signal and blinks open/closed every
    couple of seconds, so you can confirm it renders and is centred."""
    try:
        import tkinter as tk
    except Exception as e:
        _log(f"tkinter unavailable, eye cannot show: {e}")
        return 1

    try:
        root = tk.Tk()
        root.title("jarvis-eye")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        transparent = "#010203"
        try:
            root.attributes("-transparentcolor", transparent)
        except Exception:
            transparent = ""
        bg = transparent or "#0a0a0f"
        root.configure(bg=bg)

        sw = root.winfo_screenwidth()
        root.geometry(f"{W}x{H}+{(sw - W) // 2}+0")        # top-middle

        canvas = tk.Canvas(root, width=W, height=H, bg=bg, highlightthickness=0,
                           bd=0)
        canvas.pack()
        root.update_idletasks()
        _click_through(root)
        look_stop()                 # always start closed, never a stale "open"
        _log(f"eye up at top-centre, screen width {sw}")
    except Exception as e:
        _log(f"eye failed to build its window: {e}")
        return 1

    def tick():
        if _should_quit():
            _log("quit signal - closing")
            root.destroy()
            return
        try:
            looking = (int(time.time()) % 4 < 2) if demo else is_looking()
            _draw(canvas, looking)
            _raise_topmost(root)        # stay above the HUD every frame
        except Exception as e:
            _log(f"tick error (continuing): {e}")
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
        raise SystemExit(run(demo="--demo" in sys.argv))
