"""G12: an unknown app fails loudly and opens NOTHING."""
import time
import _harness as h

h.fresh_db()
import psutil
from lib import desktop

def explorer_windows():
    return sum(1 for p in psutil.process_iter(["name"])
               if (p.info.get("name") or "").lower() == "explorer.exe")

before = explorer_windows()

for bogus in ("definitely-not-an-app-zzz", "", "   "):
    try:
        r = desktop.open_app(bogus)
        h.fail(f"an unknown app {bogus!r} was 'launched': {r}")
    except ValueError as e:
        h.need(str(e), "the refusal carried no reason")

time.sleep(1.5)
after = explorer_windows()
h.need(after <= before,
       f"a failed launch opened File Explorer instead: {before} -> {after}")

# an ambiguous name must ask rather than pick one
apps = desktop.installed_apps()
h.need(len(apps) > 10, f"only {len(apps)} apps found; the lookup is broken")
h.need(all(a.get("app_id") for a in apps), "an app entry has no id to launch by")

h.ok("APP_LAUNCH_FAIL_OK")
