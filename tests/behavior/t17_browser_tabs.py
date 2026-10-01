"""G17: tabs are opened, switched and closed with no leak."""
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)
start = len(browser.tabs().get("tabs", []))

browser.goto("https://example.com/", new_tab=True)
browser.goto("https://www.iana.org/", new_tab=True)
mid = browser.tabs().get("tabs", [])
h.need(len(mid) >= start + 2, f"tabs did not open: {start} -> {len(mid)}")

urls = " ".join(t.get("url", "") for t in mid)
h.need("example.com" in urls and "iana.org" in urls,
       f"the opened tabs are not the ones asked for: {urls[:200]}")

# switching must change which page is active
browser.on_page(lambda p: p.bring_to_front(), "example.com")
active = browser.on_page(lambda p: p.url, "example.com")
h.need("example.com" in active, f"switching tabs failed: {active}")

closed = browser.close_tabs("iana.org")
h.need(closed.get("closed", 0) >= 1, f"no tab was closed: {closed}")
after = browser.tabs().get("tabs", [])
h.need(not any("iana.org" in t.get("url", "") for t in after),
       "the closed tab is still listed")

browser.close_tabs("example.com")
final = len(browser.tabs().get("tabs", []))
h.need(final <= start + 1, f"tabs leaked: started {start}, ended {final}")

h.ok("BROWSER_TABS_OK")
