"""
lib/desktop.py — the machine, at API level only. NO CLICKING.

WHAT IS DELIBERATELY ABSENT: there is no click, no type-into-another-app, no
no screen capture, no window raising. Those were 800 lines of pixel driving,
and every failure in the logs came from them - 5 to 15 seconds a step,
~1,500 tokens a perception, and a target that was a pixel coordinate rather than
an identity.

What is left is what can be done through an OS call: launching a program,
opening a file with its default handler, the media keys, and the clipboard.
These are deterministic and instant.

If a capability has no protocol path, we drop the capability rather than click
our way to it. That is the rule, and this file is what the rule leaves.
"""
from __future__ import annotations

import subprocess
import time

NO_WINDOW = 0x08000000


_APPS_CACHE: list[dict] = []


def installed_apps(refresh: bool = False) -> list[dict]:
    """Every app on the Start menu, as [{name, app_id}].

    This is the lookup table that was missing. Get-StartApps is the same list
    the Start menu searches, and it maps a human name to the AppUserModelID
    that Windows actually launches by."""
    global _APPS_CACHE
    if _APPS_CACHE and not refresh:
        return _APPS_CACHE
    ps = ("Get-StartApps | Select-Object Name,AppID | "
          "ConvertTo-Json -Compress")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
        import json
        data = json.loads(out.stdout or "[]")
        if isinstance(data, dict):
            data = [data]
        _APPS_CACHE = [{"name": d.get("Name", ""), "app_id": d.get("AppID", "")}
                       for d in data if d.get("AppID")]
    except Exception:
        _APPS_CACHE = []
    return _APPS_CACHE


def open_app(name: str) -> dict:
    """Launch an installed application by name. Refuses rather than guessing.

    THE BUG THIS REPLACES: this used to run

        explorer.exe shell:AppsFolder\\<whatever you typed>

    but shell:AppsFolder needs an exact AppUserModelID, not a friendly name.
    Given "whatsapp" explorer could not resolve it, so it fell back to doing
    the only thing it knows - IT OPENED FILE EXPLORER. Every single time, with
    no error, while reporting success. "Open WhatsApp" opened the file manager.

    Now the name is resolved against the Start menu first, and if it does not
    resolve, this RAISES. A launcher that silently opens the wrong program is
    worse than one that admits it cannot find the app."""
    name = (name or "").strip()
    if not name:
        raise ValueError("no app name")

    apps = installed_apps()
    low = name.lower()
    exact = [a for a in apps if a["name"].lower() == low]
    starts = [a for a in apps if a["name"].lower().startswith(low)]
    loose = [a for a in apps if low in a["name"].lower()]
    hits = exact or starts or loose

    if not hits:
        raise ValueError(
            f"no installed app matches {name!r}. Nothing was opened. "
            f"Use desktop.installed_apps() to see the real names.")
    if len(hits) > 1 and not exact:
        names = ", ".join(a["name"] for a in hits[:6])
        raise ValueError(
            f"{name!r} matches {len(hits)} apps ({names}) - ask which one "
            f"rather than opening the wrong program.")

    app = hits[0]

    # WhatsApp is special: launching it the ordinary way gives an app Jarvis
    # cannot then drive, because the WebView2 debugging port is only opened by
    # lib.whatsapp.start_app(). Opening it any other way would mean "open
    # WhatsApp" then "message X" fails on the second step.
    if "whatsapp" in app["name"].lower():
        from lib import whatsapp
        whatsapp.start_app()
        pid = _pid_of(APP_EXE_HINTS.get("whatsapp", "WhatsApp"))
        return {"ok": True, "launched": app["name"], "app_id": app["app_id"],
                "verified": bool(pid), "pid": pid,
                "note": "opened with its debugging port, so it is driveable"}

    # VERIFY IT ACTUALLY STARTED.
    #
    # This used to Popen explorer and return success immediately. Launching is
    # not running: the shell call returns the instant it has handed the request
    # over, so "Notepad is open" was true about the REQUEST and said nothing
    # about the program. A blocked launch, a missing package or a silent crash
    # all reported the same cheerful success.
    #
    # The process table is snapshotted first and polled for something NEW,
    # which avoids needing a name-to-executable table that would be wrong for
    # every app nobody thought of.
    before = _pid_set()
    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["app_id"]],
                     creationflags=NO_WINDOW)

    pid = _wait_for_new_process(before, app["name"], timeout=LAUNCH_TIMEOUT_S)
    if pid is None:
        return {"ok": False, "launched": app["name"], "app_id": app["app_id"],
                "verified": False, "pid": None,
                "error": (f"{app['name']} was asked to start but no new process "
                          f"appeared within {LAUNCH_TIMEOUT_S:.0f}s - it did "
                          f"not open")}
    return {"ok": True, "launched": app["name"], "app_id": app["app_id"],
            "verified": True, "pid": pid}


LAUNCH_TIMEOUT_S = 20.0
APP_EXE_HINTS = {"whatsapp": "WhatsApp"}

# Processes that always appear when anything is launched through the shell.
# Counting one of these as "the app started" would make the check meaningless.
_LAUNCH_NOISE = {"explorer.exe", "conhost.exe", "backgroundtaskhost.exe",
                 "runtimebroker.exe", "svchost.exe", "dllhost.exe",
                 "applicationframehost.exe", "startmenuexperiencehost.exe",
                 "searchhost.exe", "sihost.exe", "taskhostw.exe",
                 "wmiprvse.exe", "audiodg.exe", "ctfmon.exe"}


def _pid_set() -> set:
    import psutil
    return {p.pid for p in psutil.process_iter(["pid"])}


def _pid_of(name_fragment: str):
    import psutil
    frag = (name_fragment or "").lower()
    for p in psutil.process_iter(["pid", "name"]):
        if frag and frag in (p.info.get("name") or "").lower():
            return p.pid
    return None


def _wait_for_new_process(before: set, app_name: str, timeout: float):
    """Poll for a process that was not running before the launch.

    A condition wait, not a fixed sleep: most apps appear in a few hundred
    milliseconds and there is no reason to spend the whole budget on the ones
    that do. Prefers a new process whose name resembles the app, and falls
    back to any non-noise newcomer."""
    import psutil
    want = "".join(ch for ch in (app_name or "").lower() if ch.isalnum())
    deadline = time.time() + timeout
    fallback = None
    while time.time() < deadline:
        for p in psutil.process_iter(["pid", "name"]):
            if p.pid in before:
                continue
            name = (p.info.get("name") or "")
            if name.lower() in _LAUNCH_NOISE:
                continue
            flat = "".join(ch for ch in name.lower() if ch.isalnum())
            flat = flat[:-3] if flat.endswith("exe") else flat
            if want and (want.startswith(flat) or flat.startswith(want)):
                return p.pid                       # a confident match
            fallback = fallback or p.pid
        if fallback is not None:
            return fallback
        time.sleep(0.2)
    return None


def open_path(path: str) -> dict:
    """Open a file or folder with whatever Windows associates with it."""
    import os
    os.startfile(str(path))          # noqa: S606 - the OS handler, by design
    return {"opened": str(path)}


def media(action: str) -> dict:
    """play / pause / next / previous / volumeup / volumedown / mute.

    Virtual key events to the OS shell, not keystrokes aimed at a window - so
    there is no window to raise and nothing to get wrong."""
    import ctypes
    keys = {"play": 0xB3, "pause": 0xB3, "playpause": 0xB3,
            "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
            "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD}
    vk = keys.get((action or "").lower().replace(" ", ""))
    if vk is None:
        raise ValueError(f"unknown media action {action!r}; try {sorted(keys)}")
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return {"media": action}


def clipboard(text: str | None = None) -> str:
    """Read the clipboard, or write to it when `text` is given."""
    import ctypes
    if text is None:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                             capture_output=True, text=True, timeout=10,
                             creationflags=NO_WINDOW)
        return out.stdout.rstrip("\n")
    subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard",
                    "-Value", text], timeout=10, creationflags=NO_WINDOW)
    return text


def processes(filter: str = "") -> list[dict]:
    """What is running. Read-only."""
    import psutil
    out = []
    for p in psutil.process_iter(["name", "pid"]):
        n = p.info.get("name") or ""
        if not filter or filter.lower() in n.lower():
            out.append({"name": n, "pid": p.info["pid"]})
    return out[:60]


def os_name() -> str:
    """"Windows 11", correctly.

    platform.release() reports "10" on Windows 11 - Microsoft never bumped the
    kernel version, so Python repeats the old one and Jarvis told the Boss he
    was running Windows 10 on a Windows 11 machine. Build 22000 is the line.
    Being confidently wrong about his own computer is the exact failure this
    build exists to prevent."""
    import platform
    rel = platform.release()
    try:
        if rel == "10" and int(platform.version().split(".")[-1]) >= 22000:
            rel = "11"
    except (ValueError, IndexError):
        pass
    return f"{platform.system()} {rel}"


def stats() -> dict:
    """What this machine is and what it is doing. One call, no code to write."""
    import shutil

    import psutil
    total, used, free = shutil.disk_usage("C:/")
    vm = psutil.virtual_memory()
    return {"os": os_name(),
            "cpu_count": psutil.cpu_count(),
            "cpu_percent": psutil.cpu_percent(interval=0.3),
            "memory_percent": vm.percent,
            "memory_free_gb": round(vm.available / 1e9, 1),
            "disk_free_gb": round(free / 1e9, 1)}


# ─────────────────────────────────────────────────────────────────────────────
# Native app control — Windows UI Automation (the accessibility protocol).
#
# This is how Jarvis drives apps that have NO web view and NO API: Settings,
# File Explorer, Notepad, installers, native dialogs. It reads and clicks real
# UI ELEMENTS by their accessibility name and type - never a pixel coordinate,
# never OCR. The same principle as browser.py (the DOM) and whatsapp.py (CDP):
# address things by identity, so a window that moved or a theme that changed
# does not break anything.
#
# uiautomation wraps the same UIAutomation COM API that screen readers use.
# Imported lazily so lib/desktop costs nothing until a native app is driven.
# ─────────────────────────────────────────────────────────────────────────────

_INTERACTIVE = {"Button", "Edit", "Hyperlink", "MenuItem", "ListItem",
                "CheckBox", "RadioButton", "TabItem", "ComboBox", "TreeItem",
                "SplitButton", "Text", "Document"}


def _auto():
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(2.0)          # never hang on a missing control
    return auto


def _find_window(title: str):
    """The top-level window whose name contains `title`, case-insensitive."""
    auto = _auto()
    want = (title or "").strip().lower()
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ControlTypeName == "WindowControl" and w.Name and (
                    not want or want in w.Name.lower()):
                return w
        except Exception:
            continue
    return None


def windows() -> list[dict]:
    """Open top-level windows, as [{title, class}]. Cheaper than a screenshot."""
    auto = _auto()
    out = []
    for w in auto.GetRootControl().GetChildren():
        try:
            if w.ControlTypeName == "WindowControl" and (w.Name or "").strip():
                out.append({"title": w.Name[:80], "class": w.ClassName[:40]})
        except Exception:
            continue
    return out


def read_window(title: str, max_items: int = 50) -> dict:
    """The interactive controls of a window, so Jarvis can SEE what is in it.

    Returns name + type (+ value for fields) for buttons, fields, list items,
    menu items and the like - what a person would read and act on - rather than
    the whole accessibility tree, which is huge and mostly structure."""
    win = _find_window(title)
    if win is None:
        return {"ok": False, "error": f"no open window matches {title!r}",
                "open": [w["title"] for w in windows()][:12]}
    items: list[dict] = []

    def walk(ctrl, depth: int) -> None:
        if len(items) >= max_items or depth > 7:
            return
        try:
            kids = ctrl.GetChildren()
        except Exception:
            return
        for c in kids:
            if len(items) >= max_items:
                return
            try:
                ct = c.ControlTypeName.replace("Control", "")
                name = (c.Name or "").strip()
                val = ""
                if ct in ("Edit", "Document", "ComboBox"):
                    try:
                        vp = c.GetValuePattern()
                        val = (vp.Value or "")[:60] if vp else ""
                    except Exception:
                        val = ""
                if ct in _INTERACTIVE and (name or val):
                    items.append({"type": ct, "name": name[:60], "value": val})
                walk(c, depth + 1)
            except Exception:
                continue

    walk(win, 0)
    return {"ok": True, "window": win.Name[:80], "controls": items}


def _find_control(win, name: str, kinds=None):
    """A control under `win` whose name matches `name` (exact first, then substring)."""
    want = (name or "").strip().lower()
    best = None

    def walk(ctrl, depth: int) -> None:
        nonlocal best
        if best is not None or depth > 8:
            return
        try:
            kids = ctrl.GetChildren()
        except Exception:
            return
        for c in kids:
            if best is not None:
                return
            try:
                ct = c.ControlTypeName.replace("Control", "")
                cn = (c.Name or "").strip().lower()
                if cn and (kinds is None or ct in kinds):
                    if cn == want:
                        best = c
                        return
                    if want and want in cn and best is None:
                        best = c          # keep first substring hit as fallback
                walk(c, depth + 1)
            except Exception:
                continue

    walk(win, 0)
    return best


def click_control(title: str, control: str) -> dict:
    """Click a control in a window by its accessibility NAME, not a coordinate.

    Prefers the Invoke pattern (works without the window in front); falls back
    to a real click, bringing the window forward first."""
    win = _find_window(title)
    if win is None:
        return {"ok": False, "error": f"no open window matches {title!r}"}
    target = _find_control(win, control)
    if target is None:
        return {"ok": False,
                "error": f"no control named {control!r} in {win.Name[:50]!r}",
                "hint": "call read_window to see the real control names"}
    try:
        try:
            target.GetInvokePattern().Invoke()
            return {"ok": True, "clicked": control, "how": "invoke"}
        except Exception:
            win.SetActive()
            target.Click(simulateMove=False)
            return {"ok": True, "clicked": control, "how": "click"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def set_control(title: str, control: str, text: str) -> dict:
    """Put text into a field by name (SetValue - no keystroke races)."""
    win = _find_window(title)
    if win is None:
        return {"ok": False, "error": f"no open window matches {title!r}"}
    target = _find_control(win, control, kinds={"Edit", "Document", "ComboBox"})
    if target is None:
        return {"ok": False,
                "error": f"no text field named {control!r} in {win.Name[:50]!r}"}
    try:
        target.GetValuePattern().SetValue(text)
        return {"ok": True, "set": control, "value": text[:60]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
