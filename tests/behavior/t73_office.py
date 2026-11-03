"""G73: Jarvis can make a deck or a document itself - the light path exists.

python-pptx had been removed, so even "make me three slides on X" had nowhere to
go and every document became a delegation. office.make_pptx / make_docx restore
the do-it-yourself path, and return the real byte size so "done" means a file
that exists with content in it.
"""
from pathlib import Path

import _harness as h

h.fresh_db()
from lib import office

ws = h.workspace()

deck = office.make_pptx(
    str(ws / "review"), title="Q4 Review", subtitle="by Jarvis",
    slides=[{"title": "Revenue", "bullets": ["Up 20%", "New markets"],
             "notes": "speak to the trend"},
            {"title": "Risks", "bullets": ["Supply chain", "Hiring"]}])
h.need(deck["ok"], f"make_pptx failed: {deck}")
p = Path(deck["path"])
h.need(p.exists() and p.stat().st_size > 1000,
       f"the deck is missing or empty: {deck}")
h.need(deck["slides"] >= 2, f"the slides were not added: {deck}")

doc = office.make_docx(
    str(ws / "memo"), title="Memo",
    sections=[{"heading": "Summary", "body": "All systems go.\n\nShip it."}])
h.need(doc["ok"], f"make_docx failed: {doc}")
h.need(Path(doc["path"]).exists() and doc["bytes"] > 1000,
       f"the document is missing or empty: {doc}")

# A missing library is a clean dict the model can act on, never a crash.
import sys
real = sys.modules.pop("pptx", None)
sys.modules["pptx"] = None            # force the import to fail
try:
    bad = office.make_pptx(str(ws / "x"), slides=[])
    h.need(bad["ok"] is False and "error" in bad,
           "a missing library did not degrade to an actionable dict")
finally:
    if real is not None:
        sys.modules["pptx"] = real
    else:
        sys.modules.pop("pptx", None)

h.ok("OFFICE_OK")
