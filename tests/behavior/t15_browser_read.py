"""G15: real DOM text is read, not guessed from pixels.

Asserted against what example.com ACTUALLY serves today - it has no <h1>, only
a <p> and some <span>s. The first version of this check asserted an <h1> from
memory and failed; the page had been redesigned, and the reader was right.
"""
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)
browser.goto("https://example.com/", new_tab=True)

r = browser.read()
text = r.get("text") if isinstance(r, dict) else str(r)
h.need("documentation examples" in (text or "").lower(),
       f"body text was not read from the DOM: {str(text)[:140]!r}")
h.need(len(text or "") > 80, f"suspiciously little text: {len(text or '')}")

# a specific selector must return THAT element, not the whole page
para = browser.read("p")
ptext = para.get("text") if isinstance(para, dict) else str(para)
h.need("documentation examples" in (ptext or "").lower(),
       f"selector read failed: {str(ptext)[:140]!r}")
h.need(len(ptext or "") < len(text or ""),
       "the selector returned the whole page rather than one element")

# the title comes from the DOM, not from the URL
title = browser.on_page(lambda p: p.title(), "example.com")
h.need(title == "Example Domain", f"unexpected title: {title!r}")

# a selector matching nothing must say so rather than invent content
missing = browser.read("#definitely-not-here-xyz")
mt = missing.get("text") if isinstance(missing, dict) else str(missing)
h.need(not (mt or "").strip() or missing.get("ok") is False,
       f"a missing selector returned content: {str(missing)[:140]!r}")

h.ok("BROWSER_READ_OK")
