"""G29: destructive and financial actions still require authorisation."""
import _harness as h

h.fresh_db()
import safety

safety.end_session()

dangerous = {
    "permanently delete all my old files": "permanent_delete",
    "transfer money to Nikhil": "payment",
    "change my password on the site": "credentials",
    "message everyone in my contacts": "external_broadcast",
    "disable defender for a moment": "system_config",
}
for request, expected in dangerous.items():
    got = safety.risk_of(request)
    h.need(got == expected, f"{request!r} classified {got}, expected {expected}")
    try:
        safety.authorize(got)
        h.fail(f"{request!r} was allowed with no trusted session")
    except safety.Blocked as b:
        h.need(b.spoken and "PIN" in b.spoken,
               f"the refusal is not actionable: {b.spoken!r}")

# money is refused outright, session or no session - it was never unlockable
safety.authenticate(h.TEST_PIN)
for text in ("send money to him", "what is my cvv", "increase the ad budget"):
    try:
        safety.money_check(text)
        h.fail(f"money_check let {text!r} through")
    except safety.Blocked:
        pass

# deletion still means the recycle bin even when trusted
src = (safety.WORKSPACE / "gate-delete-me.txt")
src.parent.mkdir(parents=True, exist_ok=True)
src.write_text("bye", encoding="utf-8")
r = safety.trash(src)
h.need(r["ok"] and "recoverable" in r.get("note", ""),
       f"deletion was not recoverable: {r}")
h.need(not src.exists(), "the file was not removed")

import inspect
h.need("os.remove(" not in inspect.getsource(safety),
       "a permanent delete exists in the safety module")

h.ok("HIGH_RISK_OK")
