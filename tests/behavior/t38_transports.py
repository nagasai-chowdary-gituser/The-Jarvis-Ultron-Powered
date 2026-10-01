"""G38: every transport reaches the same agent and can genuinely act."""
import time
from pathlib import Path
import _harness as h

h.fresh_db()
import brain, safety
from safety import Session, Transport

ws = h.workspace()
results = {}

for transport in (Transport.DESK, Transport.TELEGRAM, Transport.PHONE):
    name = transport.value
    marker = f"{name}-was-here"
    b = brain.Brain(Session(transport), channel=f"chan:t38-{name}")
    t0 = time.perf_counter()
    reply = b.run(
        f"Create a file called {name}.txt in your workspace folder "
        f"'{ws.name}' containing exactly the text {marker}. "
        f"Then tell me whether it worked.")
    dt = time.perf_counter() - t0

    produced = ws / f"{name}.txt"
    h.need(produced.is_file(),
           f"{name}: the agent did not create the file, so it could not act")
    body = produced.read_text(encoding="utf-8").strip()
    h.need(marker in body, f"{name}: wrong content on disk: {body[:80]!r}")
    h.need(b.metrics["tool_calls"] >= 1, f"{name}: no tool was used at all")
    h.need(reply and reply.strip(), f"{name}: no reply")
    results[name] = {"seconds": round(dt, 1), "tools": b.metrics["tool_calls"]}

h.need(len(results) == 3, "not every transport ran")

# all three must have been able to RUN CODE - the tool that actually acts
for name, r in results.items():
    h.need(r["tools"] >= 1, f"{name} could not use a tool")

print("# " + "  ".join(f"{k}={v['seconds']}s/{v['tools']}calls"
                       for k, v in results.items()))

import shutil; shutil.rmtree(ws, ignore_errors=True)
h.ok("TRANSPORTS_OK")
