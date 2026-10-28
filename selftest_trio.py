"""
selftest_trio.py — LIVE diagnostic of the trio on your own machine.

    .venv\\Scripts\\python.exe selftest_trio.py

It makes its OWN labelled test window (never touches your real apps) and checks,
on your real screen and DPI:

  LOOK  (see_screen)        - the eyes confirm WHAT is on screen  [must pass]
  HAND  (cursor at a known  - the real click fires a real control [must pass]
         coordinate)
  GROUND(locate_on_screen)  - best-effort WHERE; REPORTED, not required, because
                              general vision models point imprecisely (live tests
                              showed gpt-4o-mini punts to screen centre). For a
                              precise click Jarvis uses the accessibility tree /
                              CDP, not this.

PASS means LOOK + HAND work (the parts the trio relies on). The GROUND line just
shows you how close the vision pointing got. Paste the output back.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except Exception:
    pass


def main() -> None:
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        print("FAIL: no OPENAI_API_KEY loaded. Run from the Jarvis folder.")
        return

    import tkinter as tk

    from lib import cursor, vision

    clicked = {"n": 0}
    root = tk.Tk()
    root.title("JARVIS TRIO SELFTEST")
    root.geometry("460x320+300+200")
    root.configure(bg="white")
    root.attributes("-topmost", True)
    tk.Label(root, text="Jarvis trio self-test", bg="white",
             font=("Segoe UI", 15)).pack(pady=12)
    btn = tk.Button(root, text="CLICK ME", font=("Segoe UI", 22, "bold"),
                    bg="#2e7d32", fg="white", width=16, height=4,
                    command=lambda: clicked.__setitem__("n", clicked["n"] + 1))
    btn.pack(expand=True, pady=20)
    root.update(); time.sleep(0.6); root.update()
    l, t = btn.winfo_rootx(), btn.winfo_rooty()
    r, b = l + btn.winfo_width(), t + btn.winfo_height()
    cx, cy = (l + r) // 2, (t + b) // 2
    start = cursor.cursor_pos()

    # LOOK (must pass) ---------------------------------------------------------
    look = vision.see_screen(
        "Is a window titled JARVIS TRIO SELFTEST visible with a green CLICK ME "
        "button? Answer yes or no and what you see.")
    ans = (look.get("answer") or look.get("error") or "")
    look_ok = look.get("ok") and ("yes" in ans.lower() or "click me" in ans.lower())
    print("LOOK   see_screen ->", ans[:140])
    print("LOOK   confirmed the button is on screen:", bool(look_ok))

    # HAND (must pass): click the KNOWN coordinate -----------------------------
    cursor.click(cx, cy)
    for _ in range(25):
        root.update(); time.sleep(0.02)
    hand_ok = clicked["n"] >= 1
    print("HAND   real click at known coord fired the control:", hand_ok)

    # GROUND (report only): how close does vision pointing get? ----------------
    clicked["n"] = 0
    loc = vision.locate("the big green CLICK ME button")
    if loc.get("found"):
        inside = l <= loc["x"] <= r and t <= loc["y"] <= b
        off = ((loc["x"] - cx) ** 2 + (loc["y"] - cy) ** 2) ** 0.5
        print(f"GROUND locate -> ({loc['x']},{loc['y']}) reliable={loc.get('reliable')} "
              f"| button centre ({cx},{cy}) | inside={inside} off_by~{off:.0f}px")
    else:
        print("GROUND locate -> not found:", loc.get("error") or loc.get("note"))
    print("GROUND (report only - Jarvis uses the accessibility tree/CDP for "
          "precise clicks, not this)")

    cursor.move(*start)
    root.destroy()
    print("\nRESULT:", "PASS - the trio's eyes (confirm) and hand work."
          if (look_ok and hand_ok) else
          "CHECK - LOOK or HAND did not pass; paste all of this back.")


if __name__ == "__main__":
    main()
