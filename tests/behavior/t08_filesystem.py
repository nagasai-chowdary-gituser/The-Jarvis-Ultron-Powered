"""G8: real files are created, read, moved, copied, searched and organised."""
from pathlib import Path
import _harness as h

h.fresh_db()
from lib import files

ws = h.workspace()
base = ws.name

files.write(f"{base}/a.txt", "hello jarvis")
h.need((ws / "a.txt").read_text(encoding="utf-8") == "hello jarvis",
       "the file on disk does not contain what was written")
h.need(files.read(f"{base}/a.txt") == "hello jarvis", "read returned wrong text")

files.append(f"{base}/a.txt", " again")
h.need((ws / "a.txt").read_text(encoding="utf-8") == "hello jarvis again",
       "append did not reach the disk")

files.mkdir(f"{base}/sub/deep")
h.need((ws / "sub" / "deep").is_dir(), "mkdir did not create the tree")

r = files.copy(f"{base}/a.txt", f"{base}/sub/b.txt")
h.need(r["ok"] and (ws / "sub" / "b.txt").is_file(), "copy did not land")
h.need((ws / "a.txt").is_file(), "copy removed the original")

r = files.move(f"{base}/sub/b.txt", f"{base}/sub/deep/c.txt")
h.need(r["ok"], f"move reported failure: {r}")
h.need((ws / "sub" / "deep" / "c.txt").is_file(), "the moved file is missing")
h.need(not (ws / "sub" / "b.txt").exists(), "move left the original behind")

hits = files.search("*.txt", base)
h.need(len(hits) >= 2, f"search found {len(hits)} of at least 2 .txt files")
inner = files.search("*.txt", base, contains="jarvis")
h.need(all("jarvis" in Path(x["path"]).read_text(encoding='utf-8').lower()
           for x in inner), "content search returned a file without the text")
h.need(len(files.search("*.nothing", base)) == 0,
       "search invented a match for a pattern nothing satisfies")

for name in ("photo.png", "sheet.csv", "movie.mp4", "weird.zzz"):
    files.write(f"{base}/{name}", "x")
plan = files.organize(base, dry_run=True)
h.need(plan["dry_run"] is True, "a dry run was not a dry run")
h.need((ws / "photo.png").is_file(), "the dry run moved a file anyway")
h.need("weird.zzz" in plan["left_alone"], "an unknown type was not left alone")

done = files.organize(base, dry_run=False)
h.need((ws / "Images" / "photo.png").is_file(), "photo was not filed")
h.need((ws / "Spreadsheets" / "sheet.csv").is_file(), "csv was not filed")
h.need((ws / "Video" / "movie.mp4").is_file(), "video was not filed")
h.need((ws / "weird.zzz").is_file(), "an unrecognised file was moved")
h.need(done["count"] >= 3, f"organize reported {done['count']} moves")

# the workspace boundary must hold for the DESTINATION too
import safety
try:
    files.move(f"{base}/a.txt", "../../escaped.txt")
    h.fail("a file was moved outside the workspace")
except safety.Blocked:
    pass

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("FILESYSTEM_OK")
