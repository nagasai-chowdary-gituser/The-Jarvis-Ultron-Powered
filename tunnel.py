"""
tunnel.py — expose the local server so Twilio can reach it, and point Twilio at it.

Twilio has to reach your machine from the public internet to deliver a call, and
your machine is behind a home router. cloudflared makes a tunnel out.

    python tunnel.py up        start a tunnel, write PUBLIC_HOST, update Twilio
    python tunnel.py status    what Twilio currently thinks the webhook is
    python tunnel.py point <https://host>   point Twilio at a URL you already have

WHY QUICK TUNNELS, AND THE CATCH
    `cloudflared tunnel --url ...` needs no account and no login, which makes it
    right for getting working today. The catch is the hostname is random and
    CHANGES EVERY RESTART, so the Twilio webhook has to be repointed each time -
    which is exactly why this script also updates Twilio rather than leaving you
    to paste URLs into a console.

    A named tunnel (`cloudflared tunnel login`) gives a permanent hostname and is
    worth doing once you know the whole thing works. Free ngrok has the same
    rotating-URL problem and a lower rate limit, which is why AGENTS.md rules it
    out.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
ENV_PATH = ROOT / ".env"
CLOUDFLARED = os.path.expandvars(r"%USERPROFILE%\.local\bin\cloudflared.exe")
# ONE source for the port, shared with main.py. Hardcoding 5051 here while
# main.py honoured JARVIS_PORT meant a tunnel could come up perfectly, point
# Twilio at itself, and forward every call to a port with nothing on it - all
# three checks passing while the phone stayed dead.
LOCAL_PORT = int(os.environ.get("JARVIS_PORT", "5051"))
LOG_PATH = ROOT / "tunnel.log"
PID_PATH = ROOT / "tunnel.pid"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def _cloudflared() -> str | None:
    import shutil
    return (CLOUDFLARED if os.path.exists(CLOUDFLARED)
            else shutil.which("cloudflared"))


def write_env(host: str) -> None:
    """Store PUBLIC_HOST without the scheme — phone.py builds wss:// from it."""
    bare = host.replace("https://", "").replace("http://", "").rstrip("/")
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    out, seen = [], False
    for ln in lines:
        if ln.startswith("PUBLIC_HOST="):
            out.append(f"PUBLIC_HOST={bare}")
            seen = True
        else:
            out.append(ln)
    if not seen:
        out.append(f"PUBLIC_HOST={bare}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["PUBLIC_HOST"] = bare


def stop() -> int:
    """Kill a cloudflared this machine started and did not clean up.

    Only ever kills the PID we wrote down, and only if that PID is still a
    cloudflared - PIDs get reused, and killing whatever inherited the number
    would be far worse than leaving a tunnel running."""
    if not PID_PATH.exists():
        return 0
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        PID_PATH.unlink(missing_ok=True)
        return 0
    killed = 0
    try:
        import psutil
        p = psutil.Process(pid)
        if "cloudflared" in (p.name() or "").lower():
            p.kill()
            killed = 1
    except Exception:
        pass
    PID_PATH.unlink(missing_ok=True)
    return killed


def start(port: int = LOCAL_PORT, timeout: float = 45.0) -> dict:
    """Start a quick tunnel and return its public URL.

    THE BUG THIS FIXES - and it made every call fail with Twilio's "an
    application error has occurred".

    A quick tunnel gets a NEW random hostname every launch, and cloudflared
    APPENDS to --logfile. This scanned that log with URL_RE.search(), which
    returns the FIRST match: after the first run that is a hostname from a
    previous session, dead the moment its tunnel closed. So each run started a
    perfectly good tunnel, read a stale URL out of the log, wrote it to .env,
    pointed the Twilio webhook at a hostname that no longer resolved, and
    printed "CALL AND IT WILL ANSWER". The log had accumulated five URLs and
    every run since the first had been advertising the first one.

    Two independent guards, because this failure is silent and end-to-end
    invisible: the log is truncated before launch, and the LAST match is taken
    rather than the first."""
    exe = _cloudflared()
    if not exe:
        return {"ok": False, "error": "cloudflared not found"}

    stop()                                  # no orphan from a previous run

    # Clear the log so there is no stale URL to read back. On Windows the
    # cloudflared we just killed can still be holding the handle for a moment,
    # so this is allowed to fail - and when it does, `before` below keeps the
    # result correct anyway by refusing to accept a URL that was already there.
    before = 0
    for _ in range(6):
        try:
            LOG_PATH.unlink(missing_ok=True)
            break
        except OSError:
            time.sleep(0.25)
    if LOG_PATH.exists():
        try:
            before = len(URL_RE.findall(
                LOG_PATH.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            before = 0

    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}",
         "--no-autoupdate", "--logfile", str(LOG_PATH)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=0x08000000,
    )
    try:
        PID_PATH.write_text(str(proc.pid), encoding="utf-8")
    except Exception:
        pass

    # Registered HERE rather than left to the caller's shutdown path: desk.py's
    # cleanup sits after a while-loop, not in a finally, so Ctrl+C skipped it and
    # every run leaked a cloudflared. atexit covers Ctrl+C and unhandled
    # exceptions; the PID file covers the rest, because a process that is killed
    # outright runs no handler at all.
    import atexit
    atexit.register(stop)

    deadline = time.time() + timeout
    while time.time() < deadline:
        # cloudflared prints the URL to its log, sometimes before stdout flushes
        try:
            source = (LOG_PATH.read_text(encoding="utf-8", errors="replace")
                      if LOG_PATH.exists() else "")
        except OSError:
            source = ""
        found = URL_RE.findall(source)
        # Strictly MORE than were there before we started, so a log we could not
        # clear cannot hand back the hostname of the tunnel we just killed.
        if len(found) > before:
            return {"ok": True, "url": found[-1], "pid": proc.pid,
                    "process": proc, "seen_urls": len(found) - before}
        if proc.poll() is not None:
            return {"ok": False, "error": f"cloudflared exited ({proc.returncode})"}
        time.sleep(0.6)

    proc.kill()
    PID_PATH.unlink(missing_ok=True)
    return {"ok": False, "error": f"no tunnel URL within {timeout:.0f}s"}


def verify(base_url: str, caller: str = "", timeout: float = 25.0) -> dict:
    """Call your own webhook from outside, exactly as Twilio will.

    Announcing "CALL AND IT WILL ANSWER" without this is how a dead hostname
    went unnoticed for five sessions. The tunnel being UP and the tunnel being
    REACHABLE are different claims, and only one of them matters."""
    import httpx
    url = base_url.rstrip("/") + "/voice"
    host = base_url.replace("https://", "").replace("http://", "").rstrip("/")
    caller = caller or os.environ.get("MY_NUMBER", "")
    deadline = time.time() + timeout
    last, reason = "no attempt made", "unknown"

    while time.time() < deadline:
        # DNS first, and reported separately. A fresh quick tunnel's hostname
        # takes a few seconds to appear, and "cannot resolve yet" is a different
        # problem from "resolves but does not route" - conflating them into one
        # ConnectError sends you looking in the wrong place.
        try:
            socket.getaddrinfo(host, 443)
        except socket.gaierror:
            last, reason = f"{host} does not resolve yet", "dns"
            time.sleep(2.0)
            continue

        try:
            # Marked as a probe so the handler answers with real TwiML but does
            # not announce an incoming call - announcing one locks the desk
            # microphone until a call that will never arrive hangs up.
            r = httpx.post(url, data={"From": caller},
                           headers={"X-Jarvis-Probe": "1"},
                           timeout=10.0, follow_redirects=True)
            body = r.text
            if r.status_code != 200:
                last, reason = f"HTTP {r.status_code} from the webhook", "http"
            elif "<Stream" in body:
                return {"ok": True, "url": url, "reachable": True,
                        "twiml": body[:200]}
            elif "<Reject" in body:
                return {"ok": False, "url": url, "reachable": True,
                        "reason": "rejected",
                        "error": "reachable, but the webhook rejected this "
                                 "caller - check MY_NUMBER matches the phone "
                                 "you call from"}
            else:
                return {"ok": False, "url": url, "reachable": True,
                        "reason": "no_stream",
                        "error": f"reachable, but returned no stream: {body[:120]}"}
        except Exception as e:
            last, reason = f"{type(e).__name__} reaching {host}", "connect"
        time.sleep(2.0)

    return {"ok": False, "url": url, "reachable": False, "reason": reason,
            "error": last}


# ─────────────────────────────────────────────────────────────────────────────
# Twilio
# ─────────────────────────────────────────────────────────────────────────────

def twilio_client():
    from twilio.rest import Client
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not sid or not tok:
        return None
    return Client(sid, tok)


def point_twilio(base_url: str) -> dict:
    """Set the Voice webhook on your Twilio number to <base>/voice."""
    c = twilio_client()
    if c is None:
        return {"ok": False, "error": "Twilio credentials not set"}
    want = os.environ.get("TWILIO_NUMBER", "").strip()
    try:
        nums = c.incoming_phone_numbers.list(limit=20)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not nums:
        return {"ok": False, "error": "this account owns no phone numbers"}

    target = next((n for n in nums if n.phone_number == want), nums[0])
    url = base_url.rstrip("/") + "/voice"
    try:
        target.update(voice_url=url, voice_method="POST")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "number": target.phone_number, "voice_url": url}


def alive() -> bool:
    """Is the cloudflared we started still running?

    THE TUNNEL WAS CHECKED ONCE, AT STARTUP, AND THEN LEFT TO ROT. If
    cloudflared died an hour into a session - a crash, a network blip, Cloudflare
    dropping a free quick tunnel - nothing noticed. Twilio stayed pointed at a
    hostname that no longer resolved, and the next call got "an application
    error has occurred". That is the owner's "sometimes the call isn't
    connecting", and it is also why a monitor could never ring: the outbound
    path needs the same tunnel, and it had quietly rotted too.

    Checks the PID rather than doing DNS, because this runs on a timer and a
    dead process is both the common cause and the free thing to look at. It
    verifies the process is still CLOUDFLARED: PIDs are reused, and treating
    whatever inherited the number as a live tunnel would be worse than no check
    at all."""
    if not PID_PATH.exists():
        return False
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return False
    try:
        import psutil
        p = psutil.Process(pid)
        return p.is_running() and "cloudflared" in (p.name() or "").lower()
    except Exception:
        return False


def status() -> dict:
    c = twilio_client()
    if c is None:
        return {"ok": False, "error": "Twilio credentials not set"}
    try:
        nums = c.incoming_phone_numbers.list(limit=20)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "numbers": [
        {"number": n.phone_number, "voice_url": n.voice_url,
         "method": n.voice_method} for n in nums]}


def _main(argv: list[str]) -> int:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
    cmd = argv[1] if len(argv) > 1 else "status"

    if cmd == "status":
        st = status()
        if not st.get("ok"):
            print(f"  {st['error']}")
            return 1
        for n in st["numbers"]:
            print(f"  {n['number']}")
            print(f"    voice webhook: {n['voice_url'] or '(not set)'} [{n['method']}]")
        print(f"\n  PUBLIC_HOST in .env: {os.environ.get('PUBLIC_HOST') or '(unset)'}")
        return 0

    if cmd == "check":
        # Answers "why did my call say application error" without guessing.
        host = os.environ.get("PUBLIC_HOST", "").strip()
        print(f"  PUBLIC_HOST in .env : {host or '(unset)'}")
        if not host:
            print("  Nothing to check. Start jarvis, or: python tunnel.py up")
            return 1

        alive = PID_PATH.exists()
        print(f"  cloudflared pid file: {'present' if alive else 'missing'}")
        try:
            import httpx
            r = httpx.get(f"http://127.0.0.1:{LOCAL_PORT}/health", timeout=5)
            print(f"  local server :{LOCAL_PORT}  : up ({r.status_code})")
        except Exception:
            print(f"  local server :{LOCAL_PORT}  : DOWN  <-- jarvis is not running.")
            print("                        Twilio gets a 502 and says")
            print("                        'an application error has occurred'.")

        v = verify(f"https://{host}", timeout=30)
        if v.get("ok"):
            print(f"  public webhook      : OK, returns a media stream")
            print("\n  Calls should connect.")
            return 0
        print(f"  public webhook      : FAILED ({v.get('reason')})")
        print(f"                        {v.get('error')}")
        st = status()
        if st.get("ok"):
            for n in st["numbers"]:
                match = "matches" if host in (n["voice_url"] or "") else "DOES NOT MATCH"
                print(f"  Twilio {n['number']}: {n['voice_url']}  [{match} .env]")
        print("\n  Restart jarvis - it starts a fresh tunnel and repoints Twilio.")
        return 1

    if cmd == "point":
        if len(argv) < 3:
            print("usage: python tunnel.py point https://your-host")
            return 2
        write_env(argv[2])
        print(" ", point_twilio(argv[2]))
        return 0

    if cmd == "up":
        print("starting cloudflared tunnel ...")
        res = start()
        if not res.get("ok"):
            print(f"  FAILED: {res['error']}")
            return 1
        url = res["url"]
        print(f"  tunnel: {url}")
        write_env(url)
        print(f"  PUBLIC_HOST written to .env")
        pt = point_twilio(url)
        if pt.get("ok"):
            print(f"  Twilio {pt['number']} -> {pt['voice_url']}")
        else:
            print(f"  Twilio NOT updated: {pt.get('error')}")
        print("\n  Tunnel is running in the background.")
        print("  Keep this window open, or the tunnel dies with it.")
        print("  Ctrl+C to stop.")
        try:
            res["process"].wait()
        except KeyboardInterrupt:
            res["process"].kill()
            print("\n  tunnel stopped")
        return 0

    print(f"unknown command {cmd!r}\nusage: python tunnel.py [up|status|point <url>]")
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
