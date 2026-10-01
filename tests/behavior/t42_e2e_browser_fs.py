"""G42: a combined browser + filesystem objective produces the real artefact."""
import time
import _harness as h

h.fresh_db()
import brain
from safety import Session, Transport

ws = h.workspace()
b = brain.Brain(Session(Transport.DESK), channel="chan:e2e2")

t0 = time.perf_counter()
reply = b.run(
    f"Open example.com in the browser, read the page text, and save it to a "
    f"file called page.txt inside your workspace folder '{ws.name}'. "
    f"Then tell me how many characters you saved.")
dt = time.perf_counter() - t0

saved = ws / "page.txt"
h.need(saved.is_file(), f"page.txt was never written. Reply: {reply[:150]!r}")
body = saved.read_text(encoding="utf-8", errors="replace")
h.need(len(body) > 50, f"only {len(body)} characters were saved")
h.need("documentation examples" in body.lower() or "example" in body.lower(),
       f"the saved text is not from example.com: {body[:120]!r}")

# the browser genuinely went there
import browser
urls = " ".join(t.get("url", "") for t in browser.tabs().get("tabs", []))
h.need("example.com" in urls, f"no example.com tab exists: {urls[:150]}")

h.need(b.metrics["tool_calls"] >= 1, "nothing was executed")
print(f"# {dt:.1f}s, {len(body)} chars saved, {b.metrics['tool_calls']} tools")

browser.close_tabs("example.com")
import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("E2E_BROWSER_FS_OK")
