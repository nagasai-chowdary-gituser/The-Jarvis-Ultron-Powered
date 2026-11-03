"""
vision.py — the eyes. Jarvis can look at the screen and read what is there.

THE CAPABILITY THAT SEPARATES AN AGENT FROM A CODE RUNNER. Without it Jarvis
opens Gmail and then has to say "tell me when you're signed in", because opening
a window and reading one are different acts and it could only do the first. With
it, it can open a thing, LOOK at the result, judge it, and act on what it saw -
which is what makes a review-and-fix loop possible at all (see lib/delegate.py).

Two functions:
    see_screen(question, window=None) -> read the screen, answer one question
    list_windows()                    -> the open window titles, cheaply

WINDOW CAPTURE USES PrintWindow, NOT ImageGrab(bbox=rect).  (§6.1)
ImageGrab screenshots the screen REGION a window occupies, so anything sitting
on top of it is captured instead - ask about WhatsApp and you read the Chrome
window over it. PrintWindow asks the window to draw ITSELF, overlap or not.
PW_RENDERFULLCONTENT (flag 3) is required or Chrome/Electron return black.

Every answer carries `source` - which capture path actually produced the image -
so Jarvis never reports on a window it did not really see. If it had to fall
back to the full screen, it says so.

This module imports cleanly on any OS; the Windows-only pieces are guarded and
degrade to a full-screen grab (mss/Pillow) so the behaviour checks still run.

SAFETY. A screenshot can carry anything on screen - an open document, a password
manager, someone else's message - off the machine to the vision model. So in the
transport gate this is RECIPE-kind, never READONLY: reachable from the desk, not
from a spoofed phone call or a Telegram message.
"""
from __future__ import annotations

import base64
import io
import os
import sys
from typing import Optional

import models

_IS_WINDOWS = sys.platform.startswith("win")

# Flag for PrintWindow. 2 is PW_RENDERFULLCONTENT; 3 = PW_CLIENTONLY|FULLCONTENT,
# which is what actually makes Chrome and Electron render instead of going black.
_PW_RENDERFULLCONTENT = 3
_MAX_EDGE = 1400          # downscale the long edge; vision cost scales with pixels


# ─────────────────────────────────────────────────────────────────────────────
# The client, made once and reused
# ─────────────────────────────────────────────────────────────────────────────

_client = None


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(timeout=45.0, max_retries=1)
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# Windows
# ─────────────────────────────────────────────────────────────────────────────

def _win_list() -> list[dict]:
    import win32gui  # type: ignore

    out: list[dict] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if title.strip():
            out.append({"hwnd": hwnd, "title": title})

    win32gui.EnumWindows(_cb, None)
    return out


def _win_find(fragment: str) -> Optional[dict]:
    frag = (fragment or "").lower().strip()
    best = None
    for w in _win_list():
        t = w["title"].lower()
        if frag in t:
            # Prefer the shortest matching title - "WhatsApp" over
            # "WhatsApp - Google Chrome" when both are open and the first is
            # the real app rather than a browser tab named after it.
            if best is None or len(w["title"]) < len(best["title"]):
                best = w
    return best


def _win_capture(hwnd):
    """PrintWindow a single window to a Pillow image. Raises on failure so the
    caller can fall back and SAY it fell back."""
    import win32gui  # type: ignore
    import win32ui  # type: ignore
    from ctypes import windll

    from PIL import Image

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 0 or h <= 0:
        raise RuntimeError("window has no area (minimised?)")

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)

    ok = windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(),
                                   _PW_RENDERFULLCONTENT)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bits,
                           "raw", "BGRX", 0, 1)

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    if not ok:
        raise RuntimeError("PrintWindow reported failure")
    return img


# ─────────────────────────────────────────────────────────────────────────────
# Cross-platform full-screen grab (fallback, and the only path off Windows)
# ─────────────────────────────────────────────────────────────────────────────

def _full_screen():
    try:
        import mss  # type: ignore
        from PIL import Image
        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[0])
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    except Exception:
        from PIL import ImageGrab  # Windows/mac only
        return ImageGrab.grab()


def _grab(window: Optional[str]):
    """Return (image, source). source names the path that actually worked, so a
    fallback is never silently reported as the window that was asked for."""
    if window and _IS_WINDOWS:
        hit = _win_find(window)
        if hit:
            try:
                return _win_capture(hit["hwnd"]), f"window:{hit['title']}"
            except Exception as e:
                # raise-then-grab: bring it forward, then region-grab it. Last
                # resort before giving up on the specific window.
                try:
                    import win32con  # type: ignore
                    import win32gui  # type: ignore
                    from PIL import ImageGrab
                    win32gui.ShowWindow(hit["hwnd"], win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hit["hwnd"])
                    rect = win32gui.GetWindowRect(hit["hwnd"])
                    return (ImageGrab.grab(bbox=rect),
                            f"raise-then-grab:{hit['title']} (PrintWindow failed: {e})")
                except Exception:
                    pass
        # fall through to full screen, and say the window was not found
    img = _full_screen()
    note = "full-screen"
    if window:
        note = f"full-screen (no window matched {window!r})"
    return img, note


# ─────────────────────────────────────────────────────────────────────────────
# Encode + ask
# ─────────────────────────────────────────────────────────────────────────────

def _encode(img) -> str:
    img = img.convert("RGB")
    if max(img.size) > _MAX_EDGE:
        scale = _MAX_EDGE / max(img.size)
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _eye(fn: str) -> None:
    """Open or close the on-screen eye overlay. Never raises - the eye is a tell
    for the owner, it must never break a capture."""
    try:
        import eye
        getattr(eye, fn)()
    except Exception:
        pass


def see_screen(question: str, window: Optional[str] = None) -> dict:
    """Look at the screen (or one window) and answer ONE question about it.

    Returns {ok, answer, source, window}. Never raises - a failure is a dict the
    model can act on, exactly like run_python. The eye overlay is held OPEN for
    the whole read and closed on every exit path, so the owner sees exactly when
    Jarvis is looking at the screen."""
    _eye("look_start")
    try:
        return _see_impl(question, window)
    finally:
        _eye("look_stop")


def _see_impl(question: str, window: Optional[str] = None) -> dict:
    q = (question or "what is on the screen right now?").strip()
    try:
        img, source = _grab(window)
    except Exception as e:
        return {"ok": False, "error": f"could not capture the screen: {e}",
                "hint": "no display, or the window was not found",
                "window": window}

    try:
        b64 = _encode(img)
    except Exception as e:
        return {"ok": False, "error": f"could not encode the screenshot: {e}"}

    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        return {"ok": False, "error": "no OPENAI_API_KEY, so I cannot read the "
                                      "image", "source": source}
    try:
        resp = _openai().responses.create(
            model=models.VISION,
            input=[{"role": "user", "content": [
                {"type": "input_text",
                 "text": ("Answer in one short factual sentence, reading real "
                          "text where you can (counts, names, subjects, error "
                          "messages). If you cannot tell, say so plainly. "
                          f"Question: {q}")},
                {"type": "input_image",
                 "image_url": f"data:image/png;base64,{b64}"},
            ]}],
            max_output_tokens=300)
        models.record(resp, models.VISION)
        answer = (resp.output_text or "").strip()
        return {"ok": True, "answer": answer, "source": source, "window": window}
    except Exception as e:
        return {"ok": False, "error": f"the vision model failed: "
                                      f"{type(e).__name__}: {e}", "source": source}


def list_windows() -> dict:
    """The open window titles. Cheaper than a screenshot, and the title often
    already holds the answer - '(3) WhatsApp' means three unread."""
    if not _IS_WINDOWS:
        return {"ok": False, "error": "window listing is Windows-only here",
                "windows": []}
    try:
        titles = [w["title"] for w in _win_list()]
        return {"ok": True, "windows": titles, "count": len(titles)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "windows": []}
