"""G16: a dead host is DETECTED, not reported as a successful navigation."""
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)

r = browser.goto("https://this-host-does-not-exist-xyz-jarvis.invalid/",
                 new_tab=True)
h.need(r.get("ok") is False or r.get("error"),
       f"navigating to a dead host reported success: {r}")

# and it must recover - a real page still works afterwards
r2 = browser.goto("https://example.com/", new_tab=False)
h.need(r2.get("ok") is not False, f"the browser did not recover: {r2}")
url = browser.on_page(lambda p: p.url, "example.com")
h.need("example.com" in (url or ""), f"recovery landed wrong: {url!r}")

h.ok("BROWSER_ERROR_OK")
