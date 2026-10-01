"""G9: real stdout, stderr and exit codes - not a launch reported as success."""
import sys
import _harness as h

h.fresh_db()
from lib import shell

r = shell.run(f'"{sys.executable}" -c "print(6*7)"')
h.need(r["ok"] is True, f"a working command was not ok: {r}")
h.need(r["exit_code"] == 0, f"exit code was {r['exit_code']}")
h.need("42" in r["stdout"], f"stdout lost: {r['stdout']!r}")

r = shell.run(f'"{sys.executable}" -c "import sys; sys.stderr.write(\'BOOM\'); sys.exit(3)"')
h.need(r["ok"] is False, "a failing command reported ok")
h.need(r["exit_code"] == 3, f"wrong exit code: {r['exit_code']}")
h.need("BOOM" in r["stderr"], f"stderr was discarded: {r['stderr']!r}")
h.need(r.get("error"), "a failure carried no reason")
h.need("BOOM" in r["error"], f"the reason ignored stderr: {r['error']!r}")

# both streams must survive together
r = shell.run(f'"{sys.executable}" -c "import sys; print(\'OUT\'); sys.stderr.write(\'ERR\')"')
h.need("OUT" in r["stdout"] and "ERR" in r["stderr"], "streams were mixed or lost")

# unicode must not crash the reader
r = shell.run(f'"{sys.executable}" -c "print(\'emoji OK\')"')
h.need(r["ok"], "a plain command failed")
r = shell.python("print('\u0c36\u0c4d\u0c30\u0c40 and \U0001f438')")
h.need(r["ok"], f"non-ascii output broke the runner: {r.get('error')}")
h.need("\U0001f438" in r["stdout"], "the emoji was lost in decoding")

# a command that does not exist must fail, not pretend
r = shell.run("this-command-does-not-exist-xyz")
h.need(r["ok"] is False, "an unknown command reported success")

# cwd must be honoured
import safety
r = shell.run(f'"{sys.executable}" -c "import os; print(os.getcwd())"',
              cwd=str(safety.WORKSPACE))
h.need(str(safety.WORKSPACE).lower() in r["stdout"].lower().strip(),
       f"cwd ignored: {r['stdout']!r}")

h.need(r["seconds"] > 0, "no duration was measured")
h.ok("TERMINAL_OK")
