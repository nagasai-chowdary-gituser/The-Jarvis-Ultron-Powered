"""
lib/phone.py — reaching the owner. Telegram message, or a real phone call.

Kept from the old notify.py (428 lines) because it worked. The call path is
Twilio, which is a real carrier call - nothing to do with WhatsApp calling,
which whatsmeow cannot do at all.

THE HOURLY CAP STAYS. Twenty watches firing in the same minute once meant
twenty phone calls; a monitor that rings that often gets its number blocked,
which silences the one alert that mattered. Over the cap it degrades to text -
the information still arrives, it just stops ringing.
"""
from __future__ import annotations

import os
import time

import httpx

MAX_CALLS_PER_HOUR = 4
_rung: list[float] = []
TIMEOUT = 15.0


def _budget_left() -> int:
    now = time.time()
    _rung[:] = [t for t in _rung if now - t < 3600]
    return max(0, MAX_CALLS_PER_HOUR - len(_rung))


def message(text: str) -> str:
    """Send the owner a Telegram message. Returns the message id - the proof."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not (token and chat):
        raise RuntimeError("Telegram is not configured")
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text[:4000]}, timeout=TIMEOUT)
    r.raise_for_status()
    mid = (r.json().get("result") or {}).get("message_id")
    if not mid:
        raise RuntimeError(f"Telegram accepted but returned no id: {r.text[:200]}")
    return str(mid)


def webhook_ready() -> dict:
    """Will a call actually be answered? Checked BEFORE dialling.

    THE FAILURE THIS PREVENTS, which the Boss heard with his own ears:

        "We are sorry, an application error has occurred."

    Twilio says that when it cannot get valid TwiML from the webhook. The call
    connected, his phone rang, he picked up expecting Jarvis - and got a
    recording. Twilio's log showed HTTP 502 from the tunnel, which means the
    tunnel was alive but NOTHING WAS LISTENING on the local port: main.py was
    not running.

    Placing a call that is guaranteed to fail is worse than refusing to place
    it, because it looks like it worked right up until he answers. So this is
    checked first, and a call that cannot be answered is never made."""
    import socket

    port = int(os.environ.get("JARVIS_PORT", "5051"))
    host = (os.environ.get("PUBLIC_HOST") or "").strip()
    host = host.replace("https://", "").replace("http://", "").rstrip("/")

    try:
        with socket.create_connection(("127.0.0.1", port), 2.0):
            local = True
    except OSError:
        local = False
    if not local:
        return {"ok": False,
                "why": f"jarvis is not listening on port {port} - main.py is "
                       f"not running, so a call would reach a dead webhook"}

    if not host or "example.com" in host:
        return {"ok": False, "why": "no public address is configured"}

    try:
        r = httpx.get(f"https://{host}/health", timeout=8.0)
        if r.status_code >= 400:
            return {"ok": False,
                    "why": f"the public address answered {r.status_code} - the "
                           f"tunnel is stale; run `python tunnel.py up`"}
    except Exception as e:
        return {"ok": False,
                "why": f"the public address {host} is unreachable "
                       f"({type(e).__name__}) - run `python tunnel.py up`"}
    return {"ok": True, "host": host}


def call_me(reason: str) -> dict:
    """Ring the owner's phone and talk. Returns the call sid - the proof.

    Pointed at the SAME /voice webhook an inbound call hits, so answering drops
    them into a real conversation rather than a recording."""
    if _budget_left() <= 0:
        raise RuntimeError(f"already rang {MAX_CALLS_PER_HOUR} times this hour")

    ready = webhook_ready()
    if not ready["ok"]:
        raise RuntimeError(
            f"NOT calling - it would ring and then play an error. {ready['why']}")
    to = (os.environ.get("MY_NUMBER") or "").strip()
    frm = (os.environ.get("TWILIO_NUMBER") or "").strip()
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    tok = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    host = (os.environ.get("PUBLIC_HOST") or "").strip().replace("https://", "")
    if not (to and frm and sid and tok):
        raise RuntimeError("Twilio or MY_NUMBER not configured")
    if not host or "example.com" in host:
        raise RuntimeError("no public address, so a conversation cannot connect")

    from urllib.parse import quote

    from twilio.rest import Client
    call = Client(sid, tok).calls.create(
        to=to, from_=frm, method="POST",
        url=f"https://{host}/voice?outbound=1&reason={quote(reason[:300])}")
    _rung.append(time.time())
    return {"sid": call.sid, "to": to[:5] + "…"}
