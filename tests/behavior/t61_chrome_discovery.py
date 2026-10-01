"""G61: Chrome instances are found and their reachability reported honestly."""
import _harness as h

h.fresh_db()
import browser

instances = browser.chrome_instances()
h.need(isinstance(instances, list), "chrome_instances did not return a list")
h.need(instances, "no Chrome found at all; this check cannot prove anything")

for c in instances:
    for key in ("pid", "port", "profile", "is_jarvis", "reachable"):
        h.need(key in c, f"an instance is missing {key}: {c}")
    h.need(c["reachable"] == bool(c["port"]),
           f"reachability disagrees with the port: {c}")
    import psutil
    h.need(psutil.pid_exists(c["pid"]), f"reported a dead pid: {c}")

mine = [c for c in instances if c["is_jarvis"]]
h.need(mine, "Jarvis's own Chrome was not identified among the instances")
h.need(all(c["reachable"] for c in mine),
       "Jarvis's own Chrome was reported unreachable, yet it is being driven")

ctx = browser.browsing_context()
for key in ("jarvis_chrome", "jarvis_tabs", "other_chromes",
            "unreachable_chromes", "can_see_owner_browser", "remedy"):
    h.need(key in ctx, f"browsing_context is missing {key}")
h.need(ctx["jarvis_chrome"] is True, "it does not know its own browser is up")
h.need(isinstance(ctx["jarvis_tabs"], list), "tabs are not listed")

# the two claims must agree with each other
unreachable = [c for c in instances if not c["reachable"]]
h.need(ctx["can_see_owner_browser"] is (not unreachable),
       f"context says can_see={ctx['can_see_owner_browser']} with "
       f"{len(unreachable)} unreachable instance(s)")
if unreachable:
    h.need(ctx["remedy"], "an unreachable browser was reported with no remedy")
    h.need("debugging port" in ctx["remedy"], f"vague remedy: {ctx['remedy'][:80]!r}")

h.ok("CHROME_DISCOVERY_OK")
