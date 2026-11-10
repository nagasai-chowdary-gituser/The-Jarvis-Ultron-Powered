"""
selftest_eye.py — proof the eye (the ULTRON orb) actually renders.

Renders one frame of the HUD headless in BOTH modes and saves them as images,
then measures how much of the orb area is lit. If the normal orb is blank on
your real screen but this script produces a bright orb_normal.png, the problem
is your machine's GPU/driver blit path, not the code - and orb_simple.png shows
exactly what the guaranteed-visible fallback (JARVIS_HUD_SIMPLE=1) looks like.

    python selftest_eye.py

Writes orb_normal.png and orb_simple.png next to this file. Open them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")      # render without a window
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _lit(hud, pygame, np) -> tuple[int, int]:
    """Lit pixels inside the orb disk, and the disk's pixel count."""
    arr = pygame.surfarray.array3d(hud.screen).astype(np.float32)   # (W, H, 3)
    w, h, _ = arr.shape
    bright = arr.max(axis=2)
    cx, cy, r = hud.cx, hud.cy, int(hud.R)
    ys, xs = np.ogrid[:h, :w]
    mask = (((xs - cx) ** 2 + (ys - cy) ** 2) <= (r * 1.1) ** 2).T
    region = bright[mask]
    return int((region > 40).sum()), int(region.size)


def main() -> int:
    import numpy as np
    import pygame

    import hud_gl
    hud_gl._prepare_display()
    hud = hud_gl.HudGL(hud_gl.HudState(), fullscreen=False)

    hud._frame()
    lit, total = _lit(hud, pygame, np)
    pct = 100 * lit / max(1, total)
    pygame.image.save(hud.screen, str(ROOT / "orb_normal.png"))
    print(f"NORMAL orb : {pct:4.1f}% of the orb area lit  -> orb_normal.png")
    normal_ok = lit > 500

    os.environ["JARVIS_HUD_SIMPLE"] = "1"
    hud._frame()
    lit2, total2 = _lit(hud, pygame, np)
    pct2 = 100 * lit2 / max(1, total2)
    pygame.image.save(hud.screen, str(ROOT / "orb_simple.png"))
    print(f"SIMPLE ring: {pct2:4.1f}% of the orb area lit  -> orb_simple.png")
    simple_ok = lit2 > 200

    print("=" * 56)
    print(f"EYE RENDERS: normal={'YES' if normal_ok else 'NO'}, "
          f"simple={'YES' if simple_ok else 'NO'}")
    if normal_ok:
        print("The orb code draws a full, bright ring. If your real screen is "
              "blank, it is the GPU/driver - run Jarvis with JARVIS_HUD_SIMPLE=1 "
              "for the guaranteed ring (orb_simple.png).")
    return 0 if (normal_ok and simple_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
