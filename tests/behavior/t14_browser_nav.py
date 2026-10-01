"""G14: the browser really navigates and the landed URL is verified."""
import _harness as h

h.fresh_db()
import browser

r = browser.ensure(auto_restart=True)
h.need(r.get("ok") is not False, f"chrome would not come up: {r}")

r = browser.goto("https://example.com/", new_tab=True)
h.need(r.get("ok") is not False, f"navigation reported failure: {r}")

url = browser.on_page(lambda p: p.url, "example.com")
h.need("example.com" in (url or ""), f"landed on the wrong page: {url!r}")

title = browser.on_page(lambda p: p.title(), "example.com")
h.need("example" in (title or "").lower(), f"unexpected title: {title!r}")

# navigating somewhere else must actually change where we are
browser.goto("https://www.iana.org/help/example-domains", new_tab=False)
url2 = browser.on_page(lambda p: p.url)
h.need(url2 != url, f"the page did not change: still {url2!r}")
h.need("iana.org" in url2, f"did not reach iana: {url2!r}")

h.ok("BROWSER_NAV_OK")
