# GATES — Jarvis "final boss": big autonomous tasks + clean codebase

Goal (owner, 2026-10-24): Jarvis must do BIG, hard, autonomous multi-step tasks
in real apps (Chrome / any big app) like a human — not toy Calculator/calendar
ops — and do them fast and without dumbness. Also: remove all unused files from
the codebase after checking carefully; keep only useful files.

## G1 — A real multi-step autonomous BROWSER task completes correctly [MET]
EVIDENCE (live, 2026-10-24): "go to news.ycombinator.com, read the top 5
stories, save to hn.txt, tell me the most interesting" -> plan -> browser
(ensure_browser, navigate, snapshot, evaluate) -> files (write_file) -> real HN
titles (FreeDNS, TornadoVM 7, neural chip, OBS 33, GrapheneOS) written to
hn.txt and an honest pick. 36s.

## G2 — A second, different big task proves generality [MET]
EVIDENCE (live): "github.com/trending -> top 3 repos + languages to
trending.txt" -> real repos (cloudflare/security-audit-skill JS, NVIDIA/
OpenShell Rust, aws/agent-toolkit-for-aws Python), 45s. AND "research latest
Python + Node.js versions, write to versions.txt, say which is higher" -> chose
the research specialist (web_search/fetch), found Python 3.15.1 + Node 24.22.1,
wrote the file, compared. 27s. Three different big tasks, all real content.

## G3 — No dead source files; nothing removed is still referenced [MET]
EVIDENCE: removed models.resolved.json (stale snapshot of models.py, zero .py
references) and scratchpad_speed.py (scratch). Confirmed kept files are live:
behavior suite passes (t01 LIFECYCLE_OK, uses current tasks.create/brain.Brain),
verify.py used by it, static/hud.html and persona.md referenced. The rebuild
had already cut 30 modules to 26 - the codebase was already lean.

## G4 — Full test suite stays green after all changes
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -m pytest tests/ -q -p no:cacheprovider
  EXPECT: passed

## G5 — Every module still imports after cleanup (no broken refs)
  CHECK: cd c:/Users/asus/Desktop/ultron && .venv/Scripts/python.exe -c "import importlib,glob,os; mods=[os.path.splitext(os.path.basename(f))[0] for f in glob.glob('*.py') if os.path.basename(f) not in ('hud_gl.py','scratchpad_speed.py')]; [importlib.import_module(m) for m in mods]; import lib.whatsapp, lib.desktop, lib.files, lib.web, lib.phone, lib.shell; print('IMPORTS_OK')"
  EXPECT: IMPORTS_OK
