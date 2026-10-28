"""
lib/vision.py — Jarvis's eyes. Seeing what has no accessibility tree.

The native path (UI Automation) and the web path (the DOM) read STRUCTURE -
button names, cell values, page text. They are blind to anything visual: an
image, a PDF, a chart, a design, a game, a canvas app, a screenshot someone
sent. A human assistant just looks. This is that: screen or image -> downscale
-> a vision model -> a sentence about what is actually there.

Used sparingly and on purpose. Reading structure is free and exact; a vision
call costs tokens and can misread small text, so the specialists prefer
read_window / the DOM and reach for this only when there is nothing structured
to read. A screenshot also carries whatever is on screen off the machine, so -
like see_screen in the old build - this is desk-only, never a spoofed phone or
Telegram request (enforced by the transport, not here).
"""
from __future__ import annotations

import base64
import io

import models

# gpt-4o-mini: vision-capable, ~5x faster and cheaper than luna on an image,
# and just as accurate reading on-screen text (verified live).
VISION_MODEL = getattr(models, "VISION_FAST", "gpt-4o-mini")
MAX_SIDE = 1400          # downscale before sending - cost scales with pixels


def _downscale(img, max_side: int = MAX_SIDE):
    w, h = img.size
    if max(w, h) > max_side:
        s = max_side / float(max(w, h))
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))))
    return img


def _b64_png(img) -> str:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _ask(img, question: str) -> str:
    from openai import OpenAI
    b64 = _b64_png(_downscale(img))
    q = question.strip() or ("Describe what is on this screen in one or two "
                             "sentences, including any important text.")
    r = OpenAI(timeout=45, max_retries=1).responses.create(
        model=VISION_MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": q},
            {"type": "input_image",
             "image_url": f"data:image/png;base64,{b64}"}]}],
        max_output_tokens=800, store=False)
    models.record(r, VISION_MODEL)
    return (r.output_text or "").strip()


def see_screen(question: str = "", window: str = "") -> dict:
    """Look at the whole screen, or one window by title, and answer `question`."""
    try:
        img = _capture_window(window) if window else _capture_screen()
    except Exception as e:
        return {"ok": False, "error": f"could not capture: {type(e).__name__}: {e}"}
    if img is None:
        return {"ok": False, "error": f"no visible window matches {window!r}"}
    try:
        return {"ok": True, "answer": _ask(img, question),
                "source": "window" if window else "screen"}
    except Exception as e:
        return {"ok": False, "error": f"vision model: {type(e).__name__}: {e}"}


def _ask_point(img, target: str) -> dict:
    """Ask the model to point at `target` in THIS image. Returns {found,x,y,what}
    in the image's own pixel coordinates, or {found: False}."""
    import json as _json
    w, h = img.size
    prompt = (
        f"This image is {w} pixels wide and {h} tall. Point at: {target}. Return "
        'ONLY JSON: {"found": true|false, "x": <int>, "y": <int>, '
        '"what": "<what is there>"} where x,y are the CENTRE of it in pixels of '
        "THIS image. If it is not visible, found=false.")
    raw = _ask(img, prompt)
    try:
        a, b = raw.find("{"), raw.rfind("}")
        obj = _json.loads(raw[a:b + 1]) if (a >= 0 and b > a) else {}
    except Exception:
        obj = {}
    if not obj.get("found"):
        return {"found": False}
    return {"found": True, "x": float(obj.get("x", 0)),
            "y": float(obj.get("y", 0)), "what": str(obj.get("what", ""))[:120]}


def locate(target: str) -> dict:
    """Find a described element on screen and return its pixel (x, y) in the
    CURSOR's coordinate space, so cursor_click can use it directly. The eyes
    telling the hand where to go.

    TWO PASSES, because a single-shot point from a general vision model is
    imprecise (verified live: it named the right button but pointed ~15% off, so
    the click missed). COARSE: point at it in the whole (downscaled) screen.
    FINE: crop a large region around that point from the FULL-RES capture and
    point again - the target is now big in frame, which is where accuracy comes
    from. All coordinates stay in the capture's PHYSICAL pixels until the final
    step converts to the cursor's logical space (so DPI scaling is handled once).
    Still best-effort: always LOOK after clicking."""
    try:
        grab = _capture_screen()
    except Exception as e:
        return {"ok": False, "error": f"could not capture: {type(e).__name__}: {e}"}
    cap_w, cap_h = grab.size
    try:
        # COARSE on the whole screen
        ds = _downscale(grab)
        coarse = _ask_point(ds, target)
    except Exception as e:
        return {"ok": False, "error": f"vision model: {type(e).__name__}: {e}"}
    if not coarse.get("found"):
        return {"ok": True, "found": False,
                "note": f"{target!r} is not visible on screen right now."}

    # coarse point -> physical capture pixels
    px = coarse["x"] * cap_w / max(1, ds.size[0])
    py = coarse["y"] * cap_h / max(1, ds.size[1])
    what = coarse.get("what", "")

    # FINE: crop a generous region (big enough to hold the target even if the
    # coarse point was off) and point again, zoomed in.
    try:
        cw, ch = int(cap_w * 0.45), int(cap_h * 0.45)
        left = int(min(max(px - cw / 2, 0), max(0, cap_w - cw)))
        top = int(min(max(py - ch / 2, 0), max(0, cap_h - ch)))
        crop = grab.crop((left, top, left + cw, top + ch))
        cds = _downscale(crop)
        fine = _ask_point(cds, target)
        if fine.get("found"):
            px = left + fine["x"] * crop.size[0] / max(1, cds.size[0])
            py = top + fine["y"] * crop.size[1] / max(1, cds.size[1])
            what = fine.get("what", what)
    except Exception:
        pass                                    # fine pass is a refinement, not required

    try:
        from lib import cursor
        w_scr, h_scr = cursor.screen_size()
        x = int(max(0, min(px * w_scr / max(1, cap_w), w_scr - 1)))
        y = int(max(0, min(py * h_scr / max(1, cap_h), h_scr - 1)))
    except Exception as e:
        return {"ok": False, "error": f"scaling: {type(e).__name__}: {e}"}

    # HONESTY: general vision models (gpt-4o-mini here) often PUNT to the image
    # centre when they cannot truly ground a point - verified live, it returned
    # dead-centre every trial while naming the target correctly. Flag that, so
    # the caller does not click a centre-punt as if it were the real location.
    near_centre = (abs(x - w_scr / 2) < w_scr * 0.06
                   and abs(y - h_scr / 2) < h_scr * 0.06)
    note = ("Best-effort only - vision pointing is imprecise; click, then LOOK "
            "to confirm, and expect to retry.")
    if near_centre:
        note = ("LOW CONFIDENCE - this looks like a centre-punt (the model could "
                "not truly locate it). Do NOT click it blindly; prefer the "
                "accessibility tree (read_window/click_control) or the browser "
                "(CDP) for a precise click.")
    return {"ok": True, "found": True, "x": x, "y": y, "what": str(what)[:120],
            "reliable": not near_centre, "note": note}


def see_image(path: str, question: str = "") -> dict:
    """Look at an image or a PDF page the Boss points at."""
    try:
        from PIL import Image
        if path.lower().endswith(".pdf"):
            img = _first_pdf_page(path)
        else:
            img = Image.open(path)
    except Exception as e:
        return {"ok": False, "error": f"cannot open {path}: {type(e).__name__}: {e}"}
    if img is None:
        return {"ok": False, "error": f"could not render {path}"}
    try:
        return {"ok": True, "answer": _ask(img, question)}
    except Exception as e:
        return {"ok": False, "error": f"vision model: {type(e).__name__}: {e}"}


# ── capture ──────────────────────────────────────────────────────────────────

def _capture_screen():
    from PIL import ImageGrab
    return ImageGrab.grab()


def _find_hwnd(title: str):
    import win32gui
    want = (title or "").lower()
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h):
            t = win32gui.GetWindowText(h)
            if t and want in t.lower():
                found.append(h)
        return True

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def _capture_window(title: str):
    """PrintWindow, NOT ImageGrab(bbox): grabbing the screen region captures
    whatever overlaps the target window; PrintWindow renders the window itself.
    PW_RENDERFULLCONTENT (flag 3) is required or Chrome/Electron come back black
    (AGENTS.md 6.1)."""
    import ctypes

    import win32gui
    import win32ui
    from PIL import Image

    hwnd = _find_hwnd(title)
    if not hwnd:
        return None
    left, top, right, bot = win32gui.GetWindowRect(hwnd)
    w, h = max(1, right - left), max(1, bot - top)
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, w, h)
    save_dc.SelectObject(bmp)
    ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
    info = bmp.GetInfo()
    bits = bmp.GetBitmapBits(True)
    img = Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                           bits, "raw", "BGRX", 0, 1)
    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)
    if not ok:                               # PrintWindow failed -> full screen
        return _capture_screen()
    return img


def _first_pdf_page(path: str):
    """Render page 1 of a PDF for the vision model. pypdfium2 if present, else
    tell the caller to use the pdf text tools instead."""
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(path)
        page = pdf[0]
        pil = page.render(scale=2.0).to_pil()
        return pil
    except Exception:
        return None
