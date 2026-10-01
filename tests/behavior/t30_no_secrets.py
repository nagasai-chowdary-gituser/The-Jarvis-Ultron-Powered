"""G30: no secret is written to the database, a log, or a task record."""
import os, re
from pathlib import Path
import _harness as h

h.fresh_db()
import safety, state, tasks

SECRETS = {k: (os.environ.get(k) or "").strip()
           for k in ("OPENAI_API_KEY", "TWILIO_AUTH_TOKEN",
                     "TELEGRAM_BOT_TOKEN", "JARVIS_PIN")}
SECRETS = {k: v for k, v in SECRETS.items() if len(v) >= 4}
h.need(SECRETS, "no secrets configured, so this check would prove nothing")

# 1. authenticating must not persist the PIN anywhere
safety.authenticate(h.TEST_PIN)
blob = ""
c = state._conn()
for (table,) in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall():
    for row in c.execute(f"SELECT * FROM {table}").fetchall():
        blob += " ".join(str(v) for v in tuple(row))
c.close()
for name, value in SECRETS.items():
    h.need(value not in blob, f"{name} was written into the database")

# 2. a task whose objective contains the PIN stores it redacted
pin = h.TEST_PIN
tid = tasks.create(f"log me in, my pin is {pin} please")
stored = tasks.get(tid)["objective"]
h.need(pin not in stored, f"the PIN survived into a task row: {stored!r}")

# 3. the same for conversation storage
state.add_message("chan:test", "in", f"my pin is {pin}")
h.need(pin not in " ".join(m["text"] for m in state.history("chan:test", 10)),
       "the PIN survived into the message history")

# 4. and nothing leaks into the real log files
for log in (Path(h.ROOT) / "worker.log", Path(h.ROOT) / "jarvis.log"):
    if not log.exists():
        continue
    text = log.read_text(encoding="utf-8", errors="replace")
    for name, value in SECRETS.items():
        h.need(value not in text, f"{name} appears in {log.name}")

# 5. an api key pattern must not appear in any source file either
key_like = re.compile(r"sk-[A-Za-z0-9_\-]{20,}")
for p in list(Path(h.ROOT).glob("*.py")) + list((Path(h.ROOT)/"lib").glob("*.py")):
    src = p.read_text(encoding="utf-8", errors="replace")
    h.need(not key_like.search(src), f"an API key is hard-coded in {p.name}")

h.ok("NO_SECRETS_OK")
