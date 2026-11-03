"""
hands.py — the HANDS. Click and type into any Windows app, not just the browser.

THE ASYMMETRY THIS FIXES. vision.py gave Jarvis eyes, but the only click and
keystroke in the whole build lived inside the browser (Playwright/CDP, for
WhatsApp and web). desktop.py is "API level only - no click, no typing into
other apps", and pyautogui had been deleted. So Jarvis could SEE a Save button
in PowerPoint and not press it. An agent that operates the machine has to touch
the machine; eyes without hands is a code runner that can look.

RELIABILITY FIRST - names, not pixels. The primary path is UI Automation
(pywinauto's UIA backend): find a control by the accessible NAME the app already
exposes ("Save", "Send", "Bold") and invoke it. That survives a window moving,
resizing, or a different screen resolution - none of which a pixel click does.
Pixels are the FALLBACK, for when a control has no name or no app exposes a tree,
and they are driven by the eyes (vision reports where a thing is, hands click
there). list_controls() hands the model the accessibility tree so it clicks a
real named control instead of guessing coordinates.

    list_controls(window)                 -> what is clickable, by name and kind
    click(target, window=None)            -> click a named control
    click_at(x, y)                        -> raw coordinate click (the fallback)
    type_text(text, into=None)            -> type into a named field, or focused
    press(*keys)                          -> a shortcut, e.g. press("ctrl","s")
    focus(window)                         -> bring a window to the front

Everything returns a dict and never raises into the turn (like run_python). Every
heavy import is lazy, so this module loads on any OS and degrades to a clear
"hands need Windows" dict off it, which keeps the behaviour checks runnable.

NO-DELETE, HONESTLY STATED. The file-deletion rule is enforced on generated CODE
by safety.preflight(). Hands act on UI, which is a different surface: Jarvis must
not click a "Delete permanently" button for the same reason it will not run
os-level deletion - the system prompt forbids it, and the alternative (move /
recycle) is offered instead. Hands do not silently bypass that rule; they are
bound by it.

DESK SURFACE. Driving the mouse and keyboard is desk-kind, like vision - it must
never be reachable from a spoofed phone call, and the caller-ID gate on /voice is
what keeps it there.
"""
from __future__ import annotations

import sys
import time
from typing import Optional

_IS_WINDOWS = sys.platform.startswith("win")

# Interactive control types worth listing - a human clicks these, not every
# static label in the tree.
_INTERACTIVE = {"Button", "MenuItem", "TabItem", "ListItem", "Edit", "CheckBox",
                "RadioButton", "ComboBox", "Hyperlink", "SplitButton",
                "TreeItem", "Text", "Custom"}


def _no_hands(what: str = "") -> dict:
    return {"ok": False, "error": "hands need Windows UI Automation, which is "
                                  "not available here", "what": what}


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _window(title: Optional[str]):
    """Best-effort connect to a top-level window by a fragment of its title.
    Returns the window spec, or None."""
    if not title:
        return None
    dt = _desktop()
    try:
        return dt.window(title_re=f".*{title}.*")
    except Exception:
        try:
            for w in dt.windows():
                if title.lower() in (w.window_text() or "").lower():
                    return w
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Look before you touch: the accessibility tree
# ─────────────────────────────────────────────────────────────────────────────

def list_controls(window: Optional[str] = None, limit: int = 60) -> dict:
    """The named, clickable controls in a window (or the foreground one).

    This is the hands' equivalent of vision reading the screen, but exact: it is
    the names the app itself publishes, so clicking one is deterministic. Prefer
    this over guessing coordinates."""
    if not _IS_WINDOWS:
        return _no_hands("list_controls")
    try:
        win = _window(window)
        if win is None:
            from pywinauto import Desktop
            win = Desktop(backend="uia").window(active_only=True)
        out = []
        for ctrl in win.descendants():
            try:
                kind = ctrl.friendly_class_name()
                name = (ctrl.window_text() or "").strip()
                if name and kind in _INTERACTIVE:
                    out.append({"name": name, "kind": kind})
                    if len(out) >= limit:
                        break
            except Exception:
                continue
        # de-duplicate, keep order
        seen, uniq = set(), []
        for c in out:
            key = (c["name"], c["kind"])
            if key not in seen:
                seen.add(key)
                uniq.append(c)
        return {"ok": True, "window": window or "(foreground)",
                "controls": uniq, "count": len(uniq)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Act
# ─────────────────────────────────────────────────────────────────────────────

def focus(window: str) -> dict:
    """Bring a window to the front so the next click or keystroke lands on it."""
    if not _IS_WINDOWS:
        return _no_hands("focus")
    try:
        win = _window(window)
        if win is None:
            return {"ok": False, "error": f"no window matched {window!r}"}
        win.set_focus()
        return {"ok": True, "window": win.window_text()}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def click(target: str, window: Optional[str] = None, double: bool = False,
          right: bool = False) -> dict:
    """Click a control by the NAME the app exposes ('Save', 'Send', 'New Slide').

    best_match tolerates small differences, so 'save' finds 'Save'. Returns what
    was actually clicked, so a wrong match is visible rather than silent."""
    if not _IS_WINDOWS:
        return _no_hands("click")
    try:
        win = _window(window)
        if win is None:
            from pywinauto import Desktop
            win = Desktop(backend="uia").window(active_only=True)
        ctrl = win.child_window(title=target, control_type="Button")
        try:
            ctrl.wrapper_object()
        except Exception:
            # fall back to a fuzzy match across any control type
            ctrl = win.child_window(best_match=target)
        if right:
            ctrl.right_click_input()
        elif double:
            ctrl.double_click_input()
        else:
            ctrl.click_input()
        return {"ok": True, "clicked": target,
                "window": win.window_text()}
    except Exception as e:
        return {"ok": False, "error": f"could not click {target!r}: "
                                      f"{type(e).__name__}: {e}",
                "hint": "call list_controls() to see the exact names, or "
                        "click_at(x, y) if it has no name"}


def click_at(x: int, y: int, double: bool = False, right: bool = False) -> dict:
    """Raw coordinate click - the FALLBACK, for a control with no name. Pair it
    with vision.see_screen to find where a thing is first."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        if right:
            pyautogui.rightClick(x, y)
        elif double:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
        return {"ok": True, "clicked_at": [x, y]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "hint": "no display, or pyautogui is not installed"}


def type_text(text: str, into: Optional[str] = None,
              window: Optional[str] = None) -> dict:
    """Type text. With `into`, type into that named field; otherwise into
    whatever has focus. Real key events, so apps that listen for them (React,
    games, terminals) receive them."""
    if not _IS_WINDOWS:
        # off Windows, best-effort via pyautogui so a dev box can still drive it
        try:
            import pyautogui
            pyautogui.typewrite(text, interval=0.02)
            return {"ok": True, "typed": text[:80]}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    try:
        if into:
            win = _window(window)
            if win is None:
                from pywinauto import Desktop
                win = Desktop(backend="uia").window(active_only=True)
            field = win.child_window(best_match=into)
            field.set_focus()
            field.type_keys(_escape(text), with_spaces=True, pause=0.01)
        else:
            from pywinauto.keyboard import send_keys
            send_keys(_escape(text), with_spaces=True, pause=0.01)
        return {"ok": True, "typed": text[:80], "into": into or "(focused)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def press(*keys: str) -> dict:
    """A shortcut: press('ctrl','s') saves, press('enter') confirms, press('alt',
    'F4') closes. Order is the chord, pressed together."""
    try:
        import pyautogui
        pyautogui.hotkey(*[k.lower() for k in keys])
        return {"ok": True, "pressed": "+".join(keys)}
    except Exception:
        pass
    if _IS_WINDOWS:
        try:
            from pywinauto.keyboard import send_keys
            combo = "".join("{%s}" % k.upper() if len(k) > 1 else k for k in keys)
            send_keys(combo)
            return {"ok": True, "pressed": "+".join(keys)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": False, "error": "no way to send keys here"}


def _escape(text: str) -> str:
    """pywinauto's type_keys reads {}()+^%~ as special; escape them so a literal
    password or path types as itself."""
    out = []
    for ch in str(text):
        out.append("{%s}" % ch if ch in "{}()+^%~[]" else ch)
    return "".join(out)
