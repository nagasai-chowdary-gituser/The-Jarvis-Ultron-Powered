"""
office.py — make a deck or a document directly, when the job does not need a
heavier tool.

The system prompt tells Jarvis to judge the weight of a job: a simple deck it
makes itself, here; an important one it may hand to ChatGPT/Claude and review
(lib/delegate.py). This is the "itself" path, and it exists because for a long
time it did not - python-pptx had been removed, so even "make me three slides on
X" had nowhere to go.

    make_pptx(path, title=None, slides=[{title, bullets, notes}], subtitle=None)
    make_docx(path, title=None, sections=[{heading, body}], body=None)

Both return a dict with the real byte size, so "done" means a file that exists
and has content - not an intention. A relative path lands in the workspace; an
absolute path is honoured, because the Boss keeps his decks on his Desktop, not
in a scratch folder.
"""
from __future__ import annotations

from pathlib import Path

import safety


def _dest(path: str | Path, suffix: str) -> Path:
    p = Path(path)
    if not p.suffix:
        p = p.with_suffix(suffix)
    full = p if p.is_absolute() else (safety.WORKSPACE / p)
    full.parent.mkdir(parents=True, exist_ok=True)
    return full


def make_pptx(path, title=None, slides=None, subtitle=None) -> dict:
    """Build a .pptx. `slides` is a list of {title, bullets:[...], notes?}.

    Returns {ok, path, slides, bytes}. bytes is the proof it was written."""
    try:
        from pptx import Presentation
        from pptx.util import Pt
    except Exception as e:
        return {"ok": False, "error": f"python-pptx is not installed: {e}",
                "hint": "pip install python-pptx"}

    full = _dest(path, ".pptx")
    prs = Presentation()
    slides = slides or []

    if title is not None:
        layout = prs.slide_layouts[0]
        s = prs.slides.add_slide(layout)
        s.shapes.title.text = str(title)
        if subtitle and len(s.placeholders) > 1:
            s.placeholders[1].text = str(subtitle)

    for item in slides:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = str(item.get("title", ""))
        body = s.placeholders[1].text_frame
        body.clear()
        bullets = item.get("bullets") or []
        for i, line in enumerate(bullets):
            para = body.paragraphs[0] if i == 0 else body.add_paragraph()
            para.text = str(line)
            para.font.size = Pt(18)
        notes = item.get("notes")
        if notes:
            s.notes_slide.notes_text_frame.text = str(notes)

    prs.save(str(full))
    size = full.stat().st_size if full.exists() else 0
    return {"ok": size > 0, "path": str(full), "bytes": size,
            "slides": len(prs.slides._sldIdLst)}


def make_docx(path, title=None, sections=None, body=None) -> dict:
    """Build a .docx. `sections` is a list of {heading, body}. Returns
    {ok, path, bytes}."""
    try:
        from docx import Document
    except Exception as e:
        return {"ok": False, "error": f"python-docx is not installed: {e}",
                "hint": "pip install python-docx"}

    full = _dest(path, ".docx")
    doc = Document()
    if title is not None:
        doc.add_heading(str(title), level=0)
    if body is not None:
        for para in str(body).split("\n\n"):
            doc.add_paragraph(para)
    for sec in (sections or []):
        if sec.get("heading"):
            doc.add_heading(str(sec["heading"]), level=1)
        if sec.get("body"):
            for para in str(sec["body"]).split("\n\n"):
                doc.add_paragraph(para)

    doc.save(str(full))
    size = full.stat().st_size if full.exists() else 0
    return {"ok": size > 0, "path": str(full), "bytes": size}
