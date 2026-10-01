"""G37: the backend really starts, serves, and shuts down with no orphans."""
import json, socket, subprocess, sys, time, urllib.request
import _harness as h

h.fresh_db()
import psutil

PORT = 5077          # not the live port, so this cannot disturb a running Jarvis

def listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

h.need(not listening(PORT), f"port {PORT} was already in use")

env = {**__import__("os").environ, "JARVIS_PORT": str(PORT),
       "PYTHONIOENCODING": "utf-8"}
proc = subprocess.Popen([sys.executable, "-u", "main.py", "--no-desk"],
                        cwd=str(h.ROOT), env=env,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")

# wait for the condition, not a fixed sleep
deadline = time.time() + 45
while time.time() < deadline and not listening(PORT):
    if proc.poll() is not None:
        out = proc.stdout.read() if proc.stdout else ""
        h.fail(f"the backend exited during startup: {out[-500:]}")
    time.sleep(0.3)
h.need(listening(PORT), "the backend never started listening")

body = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health",
                              timeout=10).read().decode()
data = json.loads(body)
h.need(data.get("ok") is True, f"health says not ok: {data}")
h.need("jobs" in data and "spend_today" in data,
       f"health is missing real fields: {data}")

# the control surface must work against the RUNNING server, not just import
def post(path, data=None):
    body = (("&".join(f"{k}={v}" for k, v in (data or {}).items())).encode()
            if data else b"")
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=body,
                                 method="POST")
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    return json.loads(urllib.request.urlopen(req, timeout=15).read().decode())

def get(path):
    return json.loads(urllib.request.urlopen(
        f"http://127.0.0.1:{PORT}{path}", timeout=15).read().decode())

listing = get("/tasks")
h.need("running" in listing and "count" in listing,
       f"/tasks does not report running work: {listing}")

# a task created in the server's own database, then cancelled over HTTP
import tasks as tasks_mod, state as state_mod
from pathlib import Path as _P
state_mod.DB_PATH = _P(h.ROOT) / "data" / "jarvis.db"
state_mod._READY = set()
tasks_mod.reset_for_tests()
tid = tasks_mod.create("startup control check", transport="desk")
tasks_mod.move(tid, tasks_mod.State.PLANNING)
tasks_mod.move(tid, tasks_mod.State.EXECUTING)

listing = get("/tasks")
h.need(any(r["id"] == tid for r in listing["running"]),
       f"the running task is not listed: {listing}")

r = post(f"/tasks/{tid}/pause")
h.need(r["ok"] and r["state"] == "paused", f"pause over HTTP failed: {r}")
r = post(f"/tasks/{tid}/resume")
h.need(r["ok"] and r["state"] == "executing", f"resume over HTTP failed: {r}")
r = post(f"/tasks/{tid}/cancel")
h.need(r["ok"] and r["state"] == "cancelled", f"cancel over HTTP failed: {r}")

tasks_mod.reset_for_tests()
h.need(tasks_mod.get(tid)["state"] is tasks_mod.State.CANCELLED,
       "the cancellation did not reach the database")

# authentication over HTTP, and the PIN must not come back
r = post("/authenticate", {"pin": "0000"})
h.need(r["ok"] is False, "a wrong PIN was accepted over HTTP")
r = post("/authenticate", {"pin": h.TEST_PIN})
h.need(r["ok"] is True and r["minutes"] == 30, f"authentication failed: {r}")
h.need(h.TEST_PIN not in json.dumps(r), f"the PIN was echoed back: {r}")

errs = get("/errors")
h.need("errors" in errs and "count" in errs, f"/errors is malformed: {errs}")

kids_before = {c.pid for c in psutil.Process(proc.pid).children(recursive=True)}

proc.terminate()
try:
    proc.wait(timeout=20)
except subprocess.TimeoutExpired:
    proc.kill(); proc.wait(timeout=10)

deadline = time.time() + 20
while time.time() < deadline and listening(PORT):
    time.sleep(0.3)
h.need(not listening(PORT), "the port is still held after shutdown")

time.sleep(1.5)
survivors = [pid for pid in kids_before if psutil.pid_exists(pid)]
h.need(not survivors, f"orphaned children after shutdown: {survivors}")
h.need(not psutil.pid_exists(proc.pid) or proc.poll() is not None,
       "the backend process itself survived")

h.ok("STARTUP_OK")
