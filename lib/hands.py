"""
hands.py — the HANDS. Click and type into any Windows app, not just the browser.

THE ASYMMETRY THIS FIXES. vision.py gave Jarvis eyes, but the only click and
keystroke in the whole build lived inside the browser (Playwright/CDP, for
WhatsApp and web). desktop.py is "API level only - no click, no typing into
other apps", and a pixel-automation library had been deleted. So Jarvis could SEE a Save button
in PowerPoint and not press it. An agent that operates the machine has to touch
the machine; eyes without hands is a code runner that can look.

ONE LIBRARY, NAMES OVER PIXELS. Everything here goes through pywinauto's UI
Automation backend - the SAME library for finding a control by name, for a raw
coordinate click, and for keystrokes. That matters for two reasons:

  1. Reliability. The primary path is a control's accessible NAME ("Save",
     "Send", "Bold"); invoking that survives a window moving, resizing, or a
     different resolution - none of which a pixel click does. list_controls()
     hands the model the accessibility tree so it clicks a real named control.
  2. The no-pixels rule. The hard-won lesson (see whatsapp.py) is that pixel
     automation is a trap, and the build forbids a pixel-automation library anywhere in lib/.
     pywinauto.mouse covers the one case names cannot - a control with NO name -
     as a documented, vision-driven fallback, without reintroducing a pixel-automation library.

    list_controls(window)                 -> what is clickable, by name and kind
    click(target, window=None)            -> click a named control
    click_at(x, y)                        -> coordinate click (the last resort)
    type_text(text, into=None)            -> type into a named field, or focused
    press(*keys)                          -> a shortcut, e.g. press("ctrl","s")
    focus(window)                         -> bring a window to the front

Everything returns a dict and never raises into the turn (like run_python). Every
heavy import is lazy, so this module loads on any OS and degrades to a clear
"hands need Windows" dict off it, which keeps the behaviour checks runnable.

NO-DELETE, HONESTLY STATED. The file-deletion rule is enforced on generated CODE
by safety.preflight(). Hands act on UI, a different surface: Jarvis must not click
a "delete permanently" button for the same reason it will not run os-level
deletion - the system prompt forbids it, and move/recycle is offered instead.

DESK SURFACE. Driving the mouse and keyboard is desk-kind, like vision - it must
never be reachable from a spoofed phone call, and the caller-ID gate on /voice is
what keeps it there.
"""
from __future__ import annotations

import re
import sys
from typing import Optional

_IS_WINDOWS = sys.platform.startswith("win")

_INTERACTIVE = {"Button", "MenuItem", "TabItem", "ListItem", "Edit", "CheckBox",
                "RadioButton", "ComboBox", "Hyperlink", "SplitButton",
                "TreeItem", "Text", "Custom"}

# send_keys syntax: ^ ctrl, % alt, + shift; special keys in braces.
_MODS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}
_SPECIAL = {"enter": "{ENTER}", "return": "{ENTER}", "tab": "{TAB}",
            "esc": "{ESC}", "escape": "{ESC}", "delete": "{DELETE}",
            "del": "{DELETE}", "backspace": "{BACKSPACE}", "space": "{SPACE}",
            "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
            "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}",
            "pagedown": "{PGDN}", "insert": "{INSERT}"}


def _no_hands(what: str = "") -> dict:
    return {"ok": False, "error": "hands need Windows UI Automation, which is "
                                  "not available here", "what": what}


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def _window(title: Optional[str]):
    """Best-effort connect to a top-level window by a fragment of its title."""
    if not title:
        return None
    dt = _desktop()
    try:
        return dt.window(title_re=f".*{re.escape(title)}.*")
    except Exception:
        try:
            for w in dt.windows():
                if title.lower() in (w.window_text() or "").lower():
                    return w
        except Exception:
            pass
    return None


def _active():
    from pywinauto import Desktop
    return Desktop(backend="uia").window(active_only=True)


# ─────────────────────────────────────────────────────────────────────────────
# Look before you touch: the accessibility tree
# ─────────────────────────────────────────────────────────────────────────────

def list_controls(window: Optional[str] = None, limit: int = 60) -> dict:
    """The named, clickable controls in a window (or the foreground one). This is
    the hands' equivalent of vision reading the screen, but exact - the names the
    app itself publishes - so clicking one is deterministic. Prefer this over
    guessing coordinates."""
    if not _IS_WINDOWS:
        return _no_hands("list_controls")
    try:
        win = _window(window) or _active()
        out, seen = [], set()
        for ctrl in win.descendants():
            try:
                kind = ctrl.friendly_class_name()
                name = (ctrl.window_text() or "").strip()
                if name and kind in _INTERACTIVE and (name, kind) not in seen:
                    seen.add((name, kind))
                    out.append({"name": name, "kind": kind})
                    if len(out) >= limit:
                        break
            except Exception:
                continue
        return {"ok": True, "window": window or "(foreground)",
                "controls": out, "count": len(out)}
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
        win = _window(window) or _active()
        ctrl = win.child_window(title=target, control_type="Button")
        try:
            ctrl.wrapper_object()
        except Exception:
            ctrl = win.child_window(best_match=target)
        if right:
            ctrl.right_click_input()
        elif double:
            ctrl.double_click_input()
        else:
            ctrl.click_input()
        return {"ok": True, "clicked": target, "window": win.window_text()}
    except Exception as e:
        return {"ok": False, "error": f"could not click {target!r}: "
                                      f"{type(e).__name__}: {e}",
                "hint": "call list_controls() to see the exact names, or "
                        "click_at(x, y) if it has no name"}


def click_at(x: int, y: int, double: bool = False, right: bool = False) -> dict:
    """Raw coordinate click - the LAST RESORT, for a control with no name. Pair it
    with vision.see_screen to find where a thing is first. Uses pywinauto.mouse,
    not a pixel-automation library, so lib/ stays free of pixel-automation libraries."""
    if not _IS_WINDOWS:
        return _no_hands("click_at")
    try:
        from pywinauto import mouse
        button = "right" if right else "left"
        if double:
            mouse.double_click(button="left", coords=(int(x), int(y)))
        else:
            mouse.click(button=button, coords=(int(x), int(y)))
        return {"ok": True, "clicked_at": [int(x), int(y)]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def type_text(text: str, into: Optional[str] = None,
              window: Optional[str] = None) -> dict:
    """Type text. With `into`, type into that named field; otherwise into whatever
    has focus. Real key events, so apps that listen for them (React, games,
    terminals) receive them."""
    if not _IS_WINDOWS:
        return _no_hands("type_text")
    try:
        keys = _escape(text)
        if into:
            field = (_window(window) or _active()).child_window(best_match=into)
            field.set_focus()
            field.type_keys(keys, with_spaces=True, pause=0.01)
        else:
            from pywinauto.keyboard import send_keys
            send_keys(keys, with_spaces=True, pause=0.01)
        return {"ok": True, "typed": text[:80], "into": into or "(focused)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def press(*keys: str) -> dict:
    """A shortcut: press('ctrl','s') saves, press('enter') confirms, press('alt',
    'F4') closes. Order is the chord, pressed together."""
    if not _IS_WINDOWS:
        return _no_hands("press")
    try:
        from pywinauto.keyboard import send_keys
        send_keys(_chord(keys))
        return {"ok": True, "pressed": "+".join(keys)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _chord(keys) -> str:
    mods, final = "", ""
    for k in keys:
        kl = str(k).lower()
        if kl in _MODS:
            mods += _MODS[kl]
        elif kl in _SPECIAL:
            final = _SPECIAL[kl]
        elif re.fullmatch(r"f\d{1,2}", kl):
            final = "{%s}" % kl.upper()
        elif len(str(k)) == 1:
            final = str(k)
        else:
            final = "{%s}" % str(k).upper()
    return mods + final


def _escape(text: str) -> str:
    """send_keys reads {}()+^%~[] as special; escape them so a literal password
    or path types as itself."""
    return "".join("{%s}" % ch if ch in "{}()+^%~[]" else ch for ch in str(text))
