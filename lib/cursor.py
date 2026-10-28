"""
lib/cursor.py — Jarvis's REAL HAND: move, click, type, scroll by screen
coordinate, through Windows SendInput.

WHY THIS, AND WHY IT IS A FALLBACK

    Almost everything is better driven by a structured contract - CDP for the
    browser, the accessibility tree for native apps. Those click the RIGHT
    element and never the one that happened to scroll under the cursor. This
    module is for what has NO such contract: a game, a Canvas-only UI, a
    stubborn installer, an ad overlay a protocol cannot reach. It is paired with
    vision (the eyes), which says WHERE to click; this is only the hand.

NO NEW DEPENDENCY. It uses the same ctypes/user32 path as the WhatsApp window
raise (keybd_event), extended with SendInput so it can type any character,
including unicode, rather than guessing virtual-key codes.

SAFETY IS AT THE TOOL LAYER, not here. A raw coordinate carries no meaning, so
this module cannot know a button says "Pay". The refusal of money/delete clicks
lives in agent_core, which has the label the vision read gave it; this module is
the mechanism it calls once that check passes. Every call is logged by the
dispatcher like any other tool.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)

# ── SendInput plumbing ────────────────────────────────────────────────────────
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

if not hasattr(wintypes, "ULONG_PTR"):
    wintypes.ULONG_PTR = wintypes.WPARAM


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", wintypes.ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", wintypes.ULONG_PTR)]


class _INUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INUNION)]


def _send(*inputs: _INPUT) -> None:
    n = len(inputs)
    arr = (_INPUT * n)(*inputs)
    _user32.SendInput(n, arr, ctypes.sizeof(_INPUT))


def _unicode_key(ch: str, up: bool) -> _INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    ki = _KEYBDINPUT(0, ord(ch), flags, 0, 0)
    return _INPUT(INPUT_KEYBOARD, _INUNION(ki=ki))


# ── YIELD TO THE OWNER ────────────────────────────────────────────────────────
# There is one physical pointer, so if the owner reaches for the mouse while
# Jarvis is using it, they must not fight. Jarvis remembers where it last put the
# pointer; before each action it checks whether the pointer has moved on its own.
# If it has (and recently), the OWNER is driving, so Jarvis yields - it raises
# UserActive and does nothing, leaving the mouse to him. A gap of more than
# _YIELD_WINDOW_S means a fresh start (no recent Jarvis action to protect), so
# the first action takes control without a false yield.

class UserActive(Exception):
    """The owner moved the mouse, so Jarvis yielded the cursor to him."""


_last_set: tuple[int, int] | None = None
_last_at: float = 0.0
_YIELD_TOL = 8          # pixels the pointer may drift before it counts as the owner
_YIELD_WINDOW_S = 4.0   # within this of Jarvis's last action, a jump means the owner


def _note_set(x: int, y: int) -> None:
    global _last_set, _last_at
    _last_set = (int(x), int(y))
    _last_at = time.time()


def reset() -> None:
    """Forget where the pointer was - call at the start of a fresh sequence."""
    global _last_set
    _last_set = None


def _check_yield() -> None:
    if _last_set is None or (time.time() - _last_at) > _YIELD_WINDOW_S:
        return                                  # fresh start: take control, no yield
    cx, cy = cursor_pos()
    if abs(cx - _last_set[0]) > _YIELD_TOL or abs(cy - _last_set[1]) > _YIELD_TOL:
        raise UserActive(
            f"the mouse moved to ({cx},{cy}) on its own - you are using it, so I "
            f"stopped. Tell me to continue and I'll pick it back up.")


# ── geometry ──────────────────────────────────────────────────────────────────

def screen_size() -> tuple[int, int]:
    return int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1))


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def _clamp(x: int, y: int) -> tuple[int, int]:
    w, h = screen_size()
    return max(0, min(int(x), w - 1)), max(0, min(int(y), h - 1))


# ── mouse ─────────────────────────────────────────────────────────────────────

_BTN = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040)}


def move(x: int, y: int) -> None:
    _check_yield()                         # did the owner grab the mouse? yield.
    x, y = _clamp(x, y)
    _user32.SetCursorPos(x, y)
    _note_set(x, y)


def click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    move(x, y)
    time.sleep(0.03)
    down, up = _BTN.get((button or "left").lower(), _BTN["left"])
    for _ in range(2 if double else 1):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.02)
        _user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.04)
    _note_set(x, y)


def scroll(amount: int) -> None:
    """Positive scrolls up, negative down. One unit ≈ one notch."""
    _check_yield()
    _user32.mouse_event(0x0800, 0, 0, int(amount) * 120, 0)   # MOUSEEVENTF_WHEEL


# ── keyboard ──────────────────────────────────────────────────────────────────

def type_text(text: str) -> None:
    """Type any text, unicode included, as real key events."""
    _check_yield()                         # if the owner took the mouse, stop
    for ch in (text or ""):
        if ch == "\n":
            press("enter")
            continue
        _send(_unicode_key(ch, False))
        _send(_unicode_key(ch, True))
        time.sleep(0.005)


_VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f11": 0x7A, "f12": 0x7B,
}


def _vk(part: str):
    part = part.strip().lower()
    if part in _VK:
        return _VK[part]
    if len(part) == 1:
        return ord(part.upper())        # a letter or digit
    return None


def press(combo: str) -> None:
    """A key or a chord: 'enter', 'ctrl+a', 'alt+F4', 'ctrl+shift+t'."""
    _check_yield()
    parts = [p for p in (combo or "").replace("-", "+").split("+") if p.strip()]
    vks = [v for v in (_vk(p) for p in parts) if v is not None]
    if not vks:
        return
    for v in vks:
        _user32.keybd_event(v, 0, 0, 0)
    time.sleep(0.02)
    for v in reversed(vks):
        _user32.keybd_event(v, 0, KEYEVENTF_KEYUP, 0)
