"""G68: everything that worked before the repair still works.

The three capabilities the owner confirmed working by hand before the failure -
WhatsApp unread, a specific WhatsApp chat, and the visible windows - are
checked directly rather than through the model, so a regression shows up as
itself rather than as a model wording change.

Where a capability needs hardware that may not be up (WhatsApp Desktop), this
reports the blocker honestly instead of passing on a skip.
"""
import time

import _harness as h

h.fresh_db()
import browser  # noqa: E402
import safety  # noqa: E402
import state  # noqa: E402
import tasks  # noqa: E402
from lib import desktop, files, shell  # noqa: E402

checked, blocked = [], []

# ── WhatsApp: unread, and one specific chat ──────────────────────────────────
from lib import whatsapp  # noqa: E402

if whatsapp.port_open():
    if whatsapp.up():
        unread = whatsapp.unread()
        h.need(isinstance(unread, list), f"unread() returned {type(unread)}")
        for row in unread:
            h.need("contact" in row and "count" in row,
                   f"malformed unread row: {row}")
        checked.append(f"whatsapp unread ({len(unread)} chat(s))")

        # a specific chat, addressed by identity and VERIFIED before reading
        who = whatsapp.open("98765 43210")
        h.need(who["ok"], f"could not open the self-chat: {who}")
        h.need(whatsapp._matches("98765 43210", who["contact"]),
               f"opened the wrong chat: {who['contact']!r}")
        hist = whatsapp.history("98765 43210", 5)
        h.need(isinstance(hist, list), "history did not return a list")
        for m in hist:
            h.need("text" in m and "mine" in m, f"malformed message: {m}")
        checked.append(f"whatsapp chat ({len(hist)} message(s))")

        # a name that matches nothing must still refuse rather than guess
        try:
            whatsapp.open("")
            h.fail("an empty contact was accepted")
        except ValueError:
            checked.append("whatsapp refuses an empty contact")
    else:
        blocked.append("WhatsApp is running but not signed in (QR screen)")
else:
    blocked.append("WhatsApp Desktop is not running with its debugging port")

# ── visible windows / processes ──────────────────────────────────────────────
procs = desktop.processes()
h.need(len(procs) > 5, f"only {len(procs)} processes visible")
h.need(all("pid" in p and "name" in p for p in procs), "malformed process rows")
h.need(desktop.processes("python"), "this very process was not visible")
checked.append(f"window/process inspection ({len(procs)} processes)")

stats = desktop.stats()
for key in ("os", "cpu_count", "cpu_percent", "memory_percent", "disk_free_gb"):
    h.need(key in stats, f"desktop.stats() lost {key}")
h.need("Windows" in stats["os"], f"wrong OS reported: {stats['os']}")
checked.append("system stats")

apps = desktop.installed_apps()
h.need(len(apps) > 10, f"only {len(apps)} apps discoverable")
checked.append(f"app discovery ({len(apps)} apps)")

# ── browser ──────────────────────────────────────────────────────────────────
browser.ensure(auto_restart=True)
browser.goto("https://example.com/", new_tab=True)
url = browser.on_page(lambda p: p.url, "example.com")
h.need("example.com" in (url or ""), f"navigation regressed: {url!r}")
r = browser.read()
text = r.get("text") if isinstance(r, dict) else str(r)
h.need("documentation examples" in (text or "").lower(), "DOM reading regressed")
tabs = browser.tabs().get("tabs", [])
h.need(tabs, "tab listing regressed")
browser.close_tabs("example.com")
checked.append("browser nav / read / tabs")

# ── terminal ─────────────────────────────────────────────────────────────────
import sys as _sys
ok = shell.run(f'"{_sys.executable}" -c "print(6*7)"')
h.need(ok["ok"] and "42" in ok["stdout"], f"terminal regressed: {ok}")
bad = shell.run(f'"{_sys.executable}" -c "import sys; sys.exit(5)"')
h.need(bad["ok"] is False and bad["exit_code"] == 5, f"exit codes regressed: {bad}")
checked.append("terminal stdout / exit codes")

# ── filesystem ───────────────────────────────────────────────────────────────
ws = h.workspace()
files.write(f"{ws.name}/r.txt", "regression")
h.need(files.read(f"{ws.name}/r.txt") == "regression", "file read/write regressed")
files.mkdir(f"{ws.name}/sub")
h.need(files.copy(f"{ws.name}/r.txt", f"{ws.name}/sub/r.txt")["ok"],
       "copy regressed")
h.need(files.move(f"{ws.name}/sub/r.txt", f"{ws.name}/moved.txt")["ok"],
       "move regressed")
h.need(files.search("*.txt", ws.name), "search regressed")
checked.append("filesystem read/write/copy/move/search")

# ── task handling ────────────────────────────────────────────────────────────
tid = tasks.create("regression task")
tasks.move(tid, tasks.State.PLANNING)
tasks.move(tid, tasks.State.EXECUTING)
tasks.cancel(tid)
h.need(tasks.get(tid)["state"] is tasks.State.CANCELLED, "cancellation regressed")
checked.append("task lifecycle and cancellation")

# ── permissions ──────────────────────────────────────────────────────────────
safety.end_session()
safety.authorize("none")                       # ordinary work: must not raise
try:
    safety.authorize("permanent_delete")
    h.fail("a high-risk action was allowed with no session")
except safety.Blocked:
    pass
h.need(safety.authenticate(h.TEST_PIN)["ok"], "authentication regressed")
safety.authorize("permanent_delete")
safety.end_session()
checked.append("permission session")

# ── honesty layer ────────────────────────────────────────────────────────────
import brain  # noqa: E402
h.need("not monitoring" in brain.vet("I'll keep monitoring.",
                                     {"job_ids": []}).lower(),
       "the honesty layer regressed")
h.need("did NOT happen" in brain.vet("Done, sent it.",
                                     {"results": [], "job_ids": []}),
       "the unearned-completion guard regressed")
checked.append("honesty layer")

# ── the new engine must not have broken the old safety rails ─────────────────
try:
    safety.safe_path("../../escape.txt")
    h.fail("the workspace boundary regressed")
except safety.Blocked:
    pass
try:
    safety.money_check("transfer money to him")
    h.fail("the money guard regressed")
except safety.Blocked:
    pass
checked.append("workspace boundary and money guard")

import shutil
shutil.rmtree(ws, ignore_errors=True)

print(f"# {len(checked)} capabilities verified:")
for c in checked:
    print(f"#   ok  {c}")
for b in blocked:
    print(f"#   BLOCKED  {b}")

h.need(len(checked) >= 9, f"only {len(checked)} capabilities checked")
h.need(not blocked,
       f"a capability could not be exercised: {blocked}. This is a real gap, "
       f"not a pass - the hardware it needs was not available.")

h.ok("REGRESSION_CAPABILITIES_OK")
