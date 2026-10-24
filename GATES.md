# GATES — Jarvis "final boss": big autonomous tasks + clean codebase

Goal (owner, 2026-10-24): Jarvis must do BIG, hard, autonomous multi-step tasks
in real apps (Chrome / any big app) like a human — not toy Calculator/calendar
ops — and do them fast and without dumbness. Also: remove all unused files from
the codebase after checking carefully; keep only useful files.

## G1 — A real multi-step autonomous BROWSER task completes correctly [manual]
Jarvis, through the orchestrator, drives Chrome via the Playwright MCP to do a
genuine multi-step research task a human would do (navigate a real site, read
it, extract real content, write a summary to a file), end to end, and the
output file contains REAL content from the live page (not invented).
EVIDENCE: the run's events show browser specialist + files specialist; the
written file in the workspace contains text that actually appears on the live
page; the spoken answer is honest about what was found.

## G2 — A second, different big task proves generality [manual]
A second multi-step task of a different shape (e.g. multi-site compare, or
browser + desktop + files together) completes correctly, verified against real
output — proving G1 was not a one-off.

## G3 — No dead source files; nothing removed is still referenced [manual]
Every removed file is confirmed unreferenced (no import, not an entry point,
not reachable via run_python/LIB.md). Kept files are all reachable.
EVIDENCE: a grep showing each removed file has zero inbound references.

## G4 — Full test suite stays green after all changes
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
  EXPECT: passed

## G5 — Every module still imports after cleanup (no broken refs)
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -c "import importlib,glob,os; mods=[os.path.splitext(os.path.basename(f))[0] for f in glob.glob('*.py') if os.path.basename(f) not in ('hud_gl.py','scratchpad_speed.py')]; [importlib.import_module(m) for m in mods]; import lib.whatsapp, lib.desktop, lib.files, lib.web, lib.phone, lib.shell; print('IMPORTS_OK')"
  EXPECT: IMPORTS_OK
