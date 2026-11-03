"""
evaluate.py — read Jarvis's own success rate back.

    python evaluate.py          # last 7 days
    python evaluate.py 30       # last 30 days

The build measured its cost to six decimals and its reliability not at all, so
asked "how good are you" it could only estimate - the invented number §1 forbids.
Every real turn now records how it ended (brain._record_outcome); this reads it
back off state.success_summary, the one query and the one formatter, so the
figure is counted, never guessed. The model reaches the same words by calling
state.success_summary() from run_python, and relays `spoken` rather than retyping
digits.
"""
from __future__ import annotations

import sys

import state


def main() -> int:
    days = 7
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        days = int(sys.argv[1])
    print(state.success_summary(days)["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
