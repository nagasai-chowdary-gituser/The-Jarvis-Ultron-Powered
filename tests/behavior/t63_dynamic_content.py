"""G63: dynamically rendered content is read by escalating, not by a parser."""
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)

# a page that builds itself in JavaScript - static HTML is a shell
browser.goto("https://music.youtube.com/", new_tab=True)
r = browser.read_dynamic("music.youtube", settle_ms=6000)
h.need(r["ok"], f"the dynamic page could not be read: {r}")
h.need(r["via"] in ("selector", "aria", "rendered-text"),
       f"unknown strategy reported: {r['via']}")
h.need(len(r.get("text") or "") > 200,
       f"only {len(r.get('text') or '')} chars read from a full app")
h.need("attempts" in r, "it did not report which strategies it tried")

# the same reader works on a plain static page - it is not site-specific
browser.goto("https://example.com/", new_tab=True)
r2 = browser.read_dynamic("example.com")
h.need(r2["ok"] and "documentation examples" in r2["text"].lower(),
       f"the reader failed on a simple page: {str(r2)[:140]}")

# a selector that exists is preferred over scraping everything
r3 = browser.read_dynamic("example.com", selector="p")
h.need(r3["ok"], f"selector read failed: {r3}")
h.need(r3["via"] == "selector", f"it ignored a working selector: {r3['via']}")
h.need(len(r3["text"]) < len(r2["text"]),
       "the selector returned the whole page")

# a selector that does not exist falls through instead of failing
r4 = browser.read_dynamic("example.com", selector="#not-here-xyz", settle_ms=1200)
h.need(r4["ok"], "a missing selector aborted the read instead of escalating")
h.need(r4["via"] != "selector", f"it claimed a missing selector worked: {r4['via']}")
h.need(any("not-here" in a for a in r4["attempts"]),
       f"the failed attempt was not reported: {r4['attempts']}")

# and there is no site-specific parser hiding in the reader
import inspect
src = inspect.getsource(browser.read_dynamic)
for banned in ("youtube", "ytmusic", "spotify"):
    h.need(banned not in src.lower(), f"the reader hard-codes {banned}")

browser.close_tabs("example.com")
h.ok("DYNAMIC_CONTENT_OK")
