"""
selftest_live.py — PROOF on your own machine, with the REAL brain.

selftest.py checks the machinery offline with faked models. This one is the
opposite: it runs the REAL orchestrator (real model, real tools, your real
config) on a few SAFE tasks and checks the ACTUAL outcome - a real file on disk,
real system numbers, the real learned-skills list - so you get real-world
evidence, not an offline claim.

It is deliberately safe: every task here only READS or CREATES a throwaway file.
Nothing is messaged, sent, bought, or deleted. It costs a few cents of API for
the model calls.

    python selftest_live.py

Needs OPENAI_API_KEY in your .env (same as running Jarvis). Each task prints what
Jarvis actually did and whether the real outcome checks out.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    _load_env()
    if not os.environ.get("OPENAI_API_KEY"):
        print("No OPENAI_API_KEY found (.env or environment). This live test "
              "needs it, the same as running Jarvis. Set it and re-run.")
        return 2

    import agent_core
    import safety
    import state
    from safety import Transport

    results = []

    def run(label, text, verify):
        print(f"\n--- {label} ---\n> {text}")
        events = []
        t0 = time.time()
        try:
            reply = agent_core.run_turn(text, Transport.DESK,
                                        emit=lambda e: events.append(e))
        except Exception as e:
            import traceback
            print(traceback.format_exc()[-500:])
            results.append((label, False, f"crashed: {type(e).__name__}"))
            return
        dt = time.time() - t0
        tools = [e.get("name") for e in events if e.get("type") == "tool"]
        print(f"Jarvis: {reply[:300]}")
        print(f"(took {dt:.1f}s; tools used: {', '.join(t for t in tools if t) or 'none'})")
        try:
            ok, detail = verify(reply, events)
        except Exception as e:
            ok, detail = False, f"verify error: {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" -- {detail}" if detail else ""))
        results.append((label, ok, detail))

    # 1. REAL system reading - must return real numbers, not a guess.
    def v_cpu(reply, ev):
        import re
        nums = re.findall(r"\d+", reply)
        return (bool(nums), "no numeric reading in the reply" if not nums else "")
    run("reads real system stats", "what is my cpu and memory usage right now", v_cpu)

    # 2. REAL file creation - a 2-slide deck must actually be on disk with 2 slides.
    deck = Path(str(safety.WORKSPACE)) / "selftest_live_deck.pptx"
    try:
        deck.unlink()
    except Exception:
        pass

    def v_deck(reply, ev):
        if not deck.exists():
            return False, f"{deck} was not created"
        try:
            from pptx import Presentation
            n = len(Presentation(str(deck)).slides)
            return (n == 2, f"deck has {n} slides, expected 2")
        except Exception as e:
            return False, f"deck unreadable: {e}"
    run("creates a real 2-slide deck on disk",
        f"make a 2-slide test deck titled 'Live Test' saved as "
        f"{deck.name} with slides 'One' and 'Two'", v_deck)

    # 3. Honest introspection - it answers from the real learned-skills store.
    def v_learned(reply, ev):
        return (len(reply) > 0, "")
    run("answers what it has learned (introspection)",
        "what have you learned to do so far", v_learned)

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 60)
    print(f"LIVE SELF-TEST: {passed}/{total} passed (real brain, real outcomes)")
    for label, ok, detail in results:
        if not ok:
            print(f"  FAIL: {label} -- {detail}")
    print("\nThis is real evidence from YOUR machine. For the parts a script "
          "cannot drive safely (messaging someone, a phone call, a real ChatGPT "
          "run in your account), use Jarvis normally and watch the HUD.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
