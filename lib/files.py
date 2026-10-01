"""
lib/files.py — reading and writing, jailed to the workspace.

Every path goes through safety.safe_path(), which is the single chokepoint.
Deletion goes to the recycle bin and nowhere else.
"""
from __future__ import annotations

from pathlib import Path

import safety


def read(path: str, max_chars: int = 20_000) -> str:
    """Read a text file from the workspace."""
    p = safety.safe_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no such file: {p}")
    return p.read_text(encoding="utf-8", errors="replace")[:max_chars]


def write(path: str, content: str) -> dict:
    """Write a text file. Returns the real size so the caller can VERIFY it."""
    p = safety.safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "bytes": p.stat().st_size}


def append(path: str, content: str) -> dict:
    p = safety.safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(content)
    return {"path": str(p), "bytes": p.stat().st_size}


def ls(path: str = ".") -> list[dict]:
    p = safety.safe_path(path)
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {p}")
    return [{"name": c.name, "dir": c.is_dir(),
             "bytes": c.stat().st_size if c.is_file() else 0}
            for c in sorted(p.iterdir())]


def exists(path: str) -> bool:
    return safety.safe_path(path).exists()


def delete(path: str) -> dict:
    """Recycle bin. Never permanent, even when authorised."""
    return safety.trash(path)


# ─────────────────────────────────────────────────────────────────────────────
# Moving things about
#
# All of it through safety.safe_path(), so a move cannot walk out of the
# workspace by way of its DESTINATION - which is the half that is easy to
# forget, because the source looks harmless.
# ─────────────────────────────────────────────────────────────────────────────

def mkdir(path: str) -> dict:
    """Create a directory and any parents. Succeeds if it already exists."""
    p = safety.safe_path(path)
    p.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(p), "existed": True}


def move(src: str, dst: str) -> dict:
    """Move a file or directory. Returns where it ACTUALLY ended up."""
    import shutil
    s = safety.safe_path(src)
    d = safety.safe_path(dst)
    if not s.exists():
        return {"ok": False, "error": f"no such path: {s}"}
    if d.is_dir():
        d = d / s.name
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"ok": d.exists() and not s.exists(), "from": str(s), "to": str(d),
            "bytes": d.stat().st_size if d.is_file() else None}


def copy(src: str, dst: str) -> dict:
    """Copy a file or directory tree. Verified by reading the result back."""
    import shutil
    s = safety.safe_path(src)
    d = safety.safe_path(dst)
    if not s.exists():
        return {"ok": False, "error": f"no such path: {s}"}
    if s.is_dir():
        shutil.copytree(str(s), str(d), dirs_exist_ok=True)
        return {"ok": d.is_dir(), "from": str(s), "to": str(d), "dir": True}
    if d.is_dir():
        d = d / s.name
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(s), str(d))
    return {"ok": d.is_file() and d.stat().st_size == s.stat().st_size,
            "from": str(s), "to": str(d), "bytes": d.stat().st_size}


def search(pattern: str = "*", path: str = ".", contains: str = "",
           limit: int = 200) -> list[dict]:
    """Find files by name pattern, and optionally by what is inside them.

    `contains` reads each candidate rather than trusting the name, because a
    file called notes.txt that does not mention the thing being looked for is
    not a match however promising it looks."""
    root = safety.safe_path(path)
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.rglob(pattern)):
        if not p.is_file():
            continue
        if contains:
            try:
                if contains.lower() not in p.read_text(
                        encoding="utf-8", errors="replace").lower():
                    continue
            except OSError:
                continue
        out.append({"name": p.name, "path": str(p),
                    "bytes": p.stat().st_size,
                    "modified": p.stat().st_mtime})
        if len(out) >= limit:
            break
    return out


# What goes where when a folder is tidied. Deliberately conservative: anything
# unrecognised is left exactly where it is rather than swept into "Other",
# because a file the agent does not understand is the one most likely to
# matter to the person who put it there.
CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt"},
    "Spreadsheets": {".xls", ".xlsx", ".csv", ".ods"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"},
    "Video": {".mp4", ".mkv", ".mov", ".avi", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".m4a", ".ogg"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi"},
}


def organize(path: str = ".", dry_run: bool = True) -> dict:
    """Sort a folder's files into category subfolders.

    Defaults to dry_run because this MOVES the owner's files. The caller sees
    exactly what would happen and asks for it explicitly; an agent that
    rearranges a directory on a guess has done something hard to undo."""
    root = safety.safe_path(path)
    if not root.is_dir():
        return {"ok": False, "error": f"not a directory: {root}"}

    plan, skipped = [], []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        dest = next((name for name, exts in CATEGORIES.items()
                     if p.suffix.lower() in exts), None)
        if dest is None:
            skipped.append(p.name)          # unrecognised: leave it alone
            continue
        plan.append({"file": p.name, "into": dest})

    if dry_run:
        return {"ok": True, "dry_run": True, "would_move": plan,
                "left_alone": skipped, "count": len(plan)}

    moved = []
    for item in plan:
        target = root / item["into"]
        target.mkdir(exist_ok=True)
        src = root / item["file"]
        dst = target / item["file"]
        if dst.exists():
            skipped.append(f"{item['file']} (already in {item['into']})")
            continue
        src.rename(dst)
        if dst.is_file():                   # verified, not assumed
            moved.append(item)
    return {"ok": True, "dry_run": False, "moved": moved,
            "left_alone": skipped, "count": len(moved)}
