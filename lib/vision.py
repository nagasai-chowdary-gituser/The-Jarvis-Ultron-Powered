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
# and just as accurate READING on-screen text (verified live). So it stays the
# eyes for see_screen / see_image - plain OCR-ish reading.
VISION_MODEL = getattr(models, "VISION_FAST", "gpt-4o-mini")

# GROUNDING is a different, harder job: "which numbered cell is this control
# in" is spatial reasoning, and gpt-4o-mini is weak at it (it used to punt to
# the image centre - the "dumb trio"). The stronger OpenAI model does it much
# better, and the Boss wants ONE OpenAI key, no second provider - so grounding
# runs on luna (the same key), reading stays on the cheap fast model. locate()
# fires only for no-handle targets, so the extra cost is rare and worth it.
GROUND_MODEL = getattr(models, "VISION", "gpt-5.6-luna")
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


def _ask(img, question: str, model: str = VISION_MODEL) -> str:
    from openai import OpenAI
    b64 = _b64_png(_downscale(img))
    q = question.strip() or ("Describe what is on this screen in one or two "
                             "sentences, including any important text.")
    r = OpenAI(timeout=45, max_retries=1).responses.create(
        model=model,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": q},
            {"type": "input_image",
             "image_url": f"data:image/png;base64,{b64}"}]}],
        max_output_tokens=800, store=False)
    models.record(r, model)
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


# ── visual grounding by SET-OF-MARKS (a numbered grid) ────────────────────────
# A general vision model cannot regress a raw pixel coordinate (gpt-4o-mini
# punted to the image centre every time). But it CAN pick a labelled cell out of
# a grid drawn on the image - that is a classification, which it does well. So we
# draw a numbered grid, ask which cell holds the target, then zoom into that cell
# and ask again on a finer grid. Coarse cell -> fine cell gives real precision
# with no new dependency (just PIL drawing).

def _draw_grid(img, cols: int, rows: int):
    """Return (image with a numbered grid drawn on it, {label: (x0,y0,x1,y1)})."""
    from PIL import ImageDraw, ImageFont
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    w, h = im.size
    cw, ch = w / cols, h / rows
    try:
        font = ImageFont.truetype("arial.ttf", max(13, int(min(cw, ch) * 0.3)))
    except Exception:
        font = ImageFont.load_default()
    boxes: dict[str, tuple] = {}
    n = 0
    for ry in range(rows):
        for cxi in range(cols):
            n += 1
            x0, y0 = cxi * cw, ry * ch
            x1, y1 = x0 + cw, y0 + ch
            boxes[str(n)] = (x0, y0, x1, y1)
            d.rectangle([x0, y0, x1, y1], outline=(255, 40, 40), width=1)
            label, lx, ly = str(n), x0 + cw / 2, y0 + ch / 2
            try:
                tb = d.textbbox((0, 0), label, font=font)
                tw, th = tb[2] - tb[0], tb[3] - tb[1]
            except Exception:
                tw, th = 10, 12
            d.rectangle([lx - tw / 2 - 3, ly - th / 2 - 2,
                         lx + tw / 2 + 3, ly + th / 2 + 2], fill=(255, 235, 0))
            d.text((lx - tw / 2, ly - th / 2 - 1), label, fill=(0, 0, 0), font=font)
    return im, boxes


def _grid_pick(img, target: str, cols: int, rows: int):
    """Ask which numbered cell holds `target`. Returns that cell's box in img
    coords, or None."""
    import json as _json
    marked, boxes = _draw_grid(img, cols, rows)
    prompt = (
        f"A grid of numbered yellow cells (1 to {cols * rows}, numbered "
        f"left-to-right then top-to-bottom) is drawn over this image. Which "
        f"SINGLE numbered cell is centred on (or most contains) {target}? "
        "Look carefully at where the target actually is; do not default to the "
        "middle of the image. If the target is simply not visible, say found "
        'false. Return ONLY JSON: {"found": true|false, "cell": <number>}.')
    raw = _ask(marked, prompt, model=GROUND_MODEL)
    try:
        a, b = raw.find("{"), raw.rfind("}")
        obj = _json.loads(raw[a:b + 1]) if (a >= 0 and b > a) else {}
    except Exception:
        obj = {}
    if not obj.get("found"):
        return None
    return boxes.get(str(obj.get("cell")))


def locate(target: str) -> dict:
    """Find a described element on screen and return its pixel (x, y) in the
    CURSOR's coordinate space, so cursor_click can use it directly - the eyes
    telling the hand where to go.

    SET-OF-MARKS, two levels. A coarse numbered grid over the whole screen picks
    the cell holding the target; a finer grid over a crop of that cell pins it
    down. Coordinates stay in the capture's PHYSICAL pixels until the final step
    converts to the cursor's logical space (DPI handled once). Still verify with
    see_screen after clicking."""
    try:
        grab = _capture_screen()
    except Exception as e:
        return {"ok": False, "error": f"could not capture: {type(e).__name__}: {e}"}
    cap_w, cap_h = grab.size

    try:
        ds = _downscale(grab)
        cell = _grid_pick(ds, target, 7, 5)          # COARSE - finer now
    except Exception as e:
        return {"ok": False, "error": f"vision model: {type(e).__name__}: {e}"}
    if cell is None:
        return {"ok": True, "found": False,
                "note": f"{target!r} is not visible on screen right now."}

    # coarse cell -> physical pixels (centre + size)
    sx, sy = cap_w / ds.size[0], cap_h / ds.size[1]
    px = (cell[0] + cell[2]) / 2 * sx
    py = (cell[1] + cell[3]) / 2 * sy
    cellw, cellh = (cell[2] - cell[0]) * sx, (cell[3] - cell[1]) * sy
    reliable = True

    # FINE: crop a region a bit larger than the coarse cell and grid it again.
    try:
        cw, ch = min(cap_w, cellw * 1.8), min(cap_h, cellh * 1.8)
        left = int(min(max(px - cw / 2, 0), max(0, cap_w - cw)))
        top = int(min(max(py - ch / 2, 0), max(0, cap_h - ch)))
        crop = grab.crop((left, top, int(left + cw), int(top + ch)))
        cds = _downscale(crop, max_side=900)
        fine = _grid_pick(cds, target, 6, 4)
        if fine is not None:
            fx = (fine[0] + fine[2]) / 2 * crop.size[0] / cds.size[0]
            fy = (fine[1] + fine[3]) / 2 * crop.size[1] / cds.size[1]
            px, py = left + fx, top + fy
        else:
            reliable = False                         # coarse only: less precise
    except Exception:
        reliable = False

    try:
        from lib import cursor
        w_scr, h_scr = cursor.screen_size()
        x = int(max(0, min(px * w_scr / max(1, cap_w), w_scr - 1)))
        y = int(max(0, min(py * h_scr / max(1, cap_h), h_scr - 1)))
    except Exception as e:
        return {"ok": False, "error": f"scaling: {type(e).__name__}: {e}"}
    return {"ok": True, "found": True, "x": x, "y": y, "what": str(target)[:120],
            "reliable": reliable,
            "note": ("Located by grid. Click, then see_screen to confirm."
                     if reliable else
                     "Coarse location only - less precise; LOOK after clicking.")}


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
