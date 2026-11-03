"""
eye.py — a small, beautiful eye at the top of the screen that OPENS when Jarvis
is looking at your screen.

Privacy you can see. A screenshot carries whatever is on screen off the machine
(§6.1), so when Jarvis uses its eyes the owner should KNOW, the moment it happens,
on any window and any desktop. This is that tell: a crafted eye (anti-aliased
iris, catch-light and glow, rendered with Pillow - not crude canvas shapes), OPEN
while vision.see_screen is reading the screen and CLOSED (a calm, glowing shut
lid) the rest of the time.

HOW THE TWO PROCESSES AGREE. vision.see_screen runs inside a run_python
subprocess; this overlay is its own process. They share a tiny flag FILE under the
workspace: see_screen writes "open <time>"/"shut <time>", this overlay polls it,
with a staleness backstop so a crashed capture cannot leave the eye stuck open.

STAYING ON TOP OF THE HUD. The ULTRON HUD is a borderless full-screen window that
is itself topmost, so this overlay re-asserts HWND_TOPMOST every tick.

WINDOWS AT RUNTIME, importable anywhere. tkinter, Pillow's ImageTk and the Win32
calls are imported lazily inside run(), so the module (and the signal functions
vision.py calls) load on any OS with no display. Startup and any crash go to
eye.log and stdout, so a window that dies on launch leaves a trace.

    python eye.py          # the eye (jarvis.bat / main.py start it for you)
    python eye.py --demo    # blink open/closed, to check it renders
"""
from __future__ import annotations

import math
import os
import sys
import time
from pathlib import Path

import safety

EYE_FILE = safety.WORKSPACE / ".jarvis_eye"
LOG = safety.ROOT / "eye.log"
_FRESH_S = 25.0
W = H = 66                      # slightly smaller than before; glow fades inside


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} eye: {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
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
    _write("open")


def look_stop() -> None:
    _write("shut")


def look_quit() -> None:
    _write("quit")


def _read() -> tuple[str, float]:
    try:
        parts = EYE_FILE.read_text(encoding="utf-8").split()
        return parts[0], float(parts[1])
    except Exception:
        return "shut", 0.0


def is_looking() -> bool:
    state, ts = _read()
    return state == "open" and (time.time() - ts) < _FRESH_S


def _should_quit() -> bool:
    return _read()[0] == "quit"


# ─────────────────────────────────────────────────────────────────────────────
# The drawing — Pillow, supersampled for smooth edges
# ─────────────────────────────────────────────────────────────────────────────

def _almond(cx, cy, hw, hh, scale=1.0, n=90):
    pts = []
    for i in range(n + 1):
        k = -1 + 2 * i / n
        pts.append((cx + k * hw, cy - hh * scale * math.cos(k * math.pi / 2) ** 0.85))
    for i in range(n + 1):
        k = 1 - 2 * i / n
        pts.append((cx + k * hw, cy + hh * scale * math.cos(k * math.pi / 2) ** 0.85))
    return pts


def eye_image(looking: bool, px: int):
    """A crafted eye as an RGBA image. Rendered at 5x and downscaled, so the edges
    are smooth. The eye sits inside the frame with a margin, so its glow fades to
    fully transparent before the window edge - no halo box on any wallpaper."""
    from PIL import Image, ImageDraw, ImageFilter, ImageChops
    S = px * 5
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    cx, cy = S / 2, S / 2
    hw, hh = S * 0.35, S * 0.235          # leaves room for the glow

    if looking:
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).polygon(_almond(cx, cy, hw, hh, 1.16),
                                     fill=(46, 255, 130, 80))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.028)))

        mask = Image.new("L", (S, S), 0)
        ImageDraw.Draw(mask).polygon(_almond(cx, cy, hw, hh, 1.0), fill=255)

        interior = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(interior)
        d.rectangle([0, 0, S, S], fill=(5, 18, 14, 255))
        ir = S * 0.205

        def lerp(a, b, t):
            return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

        edge, mid, inner = (4, 40, 30), (46, 196, 120), (10, 70, 56)
        for r in range(int(ir), 0, -1):
            t = r / ir
            col = (lerp(mid, edge, (t - 0.62) / 0.38) if t > 0.62
                   else lerp(inner, mid, t / 0.62))
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col + (255,))
        fib = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        fd = ImageDraw.Draw(fib)
        for a in range(0, 360, 4):
            ra = math.radians(a)
            fd.line([cx + ir * 0.45 * math.cos(ra), cy + ir * 0.45 * math.sin(ra),
                     cx + ir * 0.9 * math.cos(ra), cy + ir * 0.9 * math.sin(ra)],
                    fill=(180, 255, 210, 40), width=max(1, S // 360))
        interior.alpha_composite(fib.filter(ImageFilter.GaussianBlur(S * 0.004)))
        d.ellipse([cx - ir, cy - ir, cx + ir, cy + ir], outline=(3, 26, 20, 255),
                  width=max(2, S // 90))
        pr = ir * 0.42
        d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(1, 4, 3, 255))
        d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], outline=(60, 150, 110, 120),
                  width=max(1, S // 240))
        hl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        hx, hy, hr = cx - ir * 0.36, cy - ir * 0.46, ir * 0.26
        ImageDraw.Draw(hl).ellipse([hx - hr, hy - hr * 0.78, hx + hr, hy + hr * 0.78],
                                   fill=(235, 255, 245, 210))
        interior.alpha_composite(hl.filter(ImageFilter.GaussianBlur(S * 0.012)))
        sp = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        sr = ir * 0.08
        ImageDraw.Draw(sp).ellipse([cx + ir * 0.18 - sr, cy + ir * 0.18 - sr,
                                    cx + ir * 0.18 + sr, cy + ir * 0.18 + sr],
                                   fill=(255, 255, 255, 230))
        interior.alpha_composite(sp.filter(ImageFilter.GaussianBlur(S * 0.004)))
        interior.putalpha(ImageChops.multiply(interior.getchannel("A"), mask))
        img.alpha_composite(interior)

        lid = _almond(cx, cy, hw, hh, 1.0)
        ol = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(ol).line(lid + [lid[0]], fill=(130, 255, 170, 255),
                                width=max(3, S // 85), joint="curve")
        img.alpha_composite(ol.filter(ImageFilter.GaussianBlur(S * 0.004)))
    else:
        ghost = _almond(cx, cy, hw, hh, 1.0)
        g = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(g).line(ghost + [ghost[0]], fill=(40, 120, 110, 55),
                               width=max(2, S // 150), joint="curve")
        img.alpha_composite(g.filter(ImageFilter.GaussianBlur(S * 0.006)))
        lid = [(cx + (-1 + 2 * i / 90) * hw * 0.98,
                cy + hh * 0.36 * math.cos((-1 + 2 * i / 90) * math.pi / 2))
               for i in range(91)]
        glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(glow).line(lid, fill=(70, 205, 190, 120),
                                  width=max(6, S // 60), joint="curve")
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(S * 0.03)))
        d = ImageDraw.Draw(img)
        d.line(lid, fill=(150, 240, 225, 255), width=max(4, S // 85), joint="curve")
        for k in (-0.5, -0.17, 0.17, 0.5):
            x = cx + k * hw * 0.9
            y = cy + hh * 0.36 * math.cos(k * math.pi / 2)
            d.line([x, y, x + k * hw * 0.05, y + hh * 0.3],
                   fill=(90, 190, 180, 220), width=max(2, S // 150))

    return img.resize((px, px), Image.LANCZOS)


# ─────────────────────────────────────────────────────────────────────────────
# The overlay
# ─────────────────────────────────────────────────────────────────────────────

def _hwnd(root):
    import ctypes
    wid = root.winfo_id()
    return ctypes.windll.user32.GetParent(wid) or wid


def _click_through(root) -> None:
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
        _log(f"click_through not applied (not fatal): {e}")


def _raise_topmost(root) -> None:
    try:
        import ctypes
        SWP = 0x0001 | 0x0002 | 0x0010      # NOSIZE | NOMOVE | NOACTIVATE
        ctypes.windll.user32.SetWindowPos(_hwnd(root), -1, 0, 0, 0, 0, SWP)
    except Exception:
        pass


def _hex(c: str):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def run(demo: bool = False) -> int:
    _log(f"starting, pid {os.getpid()}, python {sys.executable}")
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
    except Exception:
        import traceback
        _log("tkinter or Pillow unavailable, eye cannot show:\n"
             + traceback.format_exc())
        return 1

    try:
        root = tk.Tk()
        root.title("jarvis-eye")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        transparent = ""
        try:
            root.attributes("-transparentcolor", "#010203")
            transparent = "#010203"
        except Exception as e:
            _log(f"transparent window unsupported, using a solid badge: {e}")
        bg = transparent or "#07120b"
        root.configure(bg=bg)
        sw = root.winfo_screenwidth()
        x = (sw - W) // 2
        root.geometry(f"{W}x{H}+{x}+0")
        canvas = tk.Canvas(root, width=W, height=H, bg=bg, highlightthickness=0, bd=0)
        canvas.pack()

        base = _hex(transparent or bg)
        photos = {}
        for state in (True, False):
            flat = Image.new("RGB", (W, H), base)
            eye = eye_image(state, W)
            flat.paste(eye, (0, 0), eye)
            photos[state] = ImageTk.PhotoImage(flat)
        item = canvas.create_image(W // 2, H // 2, image=photos[False])

        root.update_idletasks()
        _click_through(root)
        look_stop()
        _log(f"UP at top-centre: x={x}, screen {sw}px, "
             f"mode={'transparent' if transparent else 'SOLID badge'}")
    except Exception:
        import traceback
        _log("FAILED to build the window:\n" + traceback.format_exc())
        return 1

    def tick():
        if _should_quit():
            _log("quit signal - closing")
            root.destroy()
            return
        try:
            looking = (int(time.time()) % 4 < 2) if demo else is_looking()
            canvas.itemconfig(item, image=photos[looking])
            _raise_topmost(root)
        except Exception as e:
            _log(f"tick error (continuing): {e}")
        root.after(180, tick)

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
