"""
main.py — the doors. Telegram, phone, and the desk HUD's socket. No logic.

Thin on purpose. Every entry point does the same three things: build a Session
with the right Transport, hand the text to Brain, send back what it returns.
Anything that looks like a decision belongs in brain.py, and anything that
waits belongs in worker.py.

    python main.py            everything
    python main.py --no-phone Telegram + HUD only
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

import httpx
from fastapi import APIRouter, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse

import brain
import realtime
import safety
import state
import tasks
import tunnel
from safety import Session, Transport

app = FastAPI()
router = APIRouter()
LOCAL_PORT = int(os.environ.get("JARVIS_PORT", "5051"))


# ─────────────────────────────────────────────────────────────────────────────
# The HUD socket — it RECEIVES state and renders. It never decides anything.
# ─────────────────────────────────────────────────────────────────────────────

class Hub:
    """Fan-out to every connected display client."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def join(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def leave(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send(self, event: dict) -> None:
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(json.dumps(event, default=str))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.leave(ws)


hub = Hub()
_loop: asyncio.AbstractEventLoop | None = None


def emit(event: dict) -> None:
    """Called from any thread. The HUD is a display, so this never blocks."""
    if _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(hub.send(event), _loop)
    except Exception:
        pass


@app.websocket("/hud")
async def hud_socket(ws: WebSocket) -> None:
    await hub.join(ws)
    try:
        await ws.send_text(json.dumps({"type": "hello", "db": str(state.DB_PATH)}))
        while True:
            await ws.receive_text()          # display clients do not send commands
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(ws)


# ─────────────────────────────────────────────────────────────────────────────
# One way in for every channel
# ─────────────────────────────────────────────────────────────────────────────

async def handle(text: str, transport: Transport, jid: str = "") -> str:
    """One message in, one reply out - but NOT one conversation each.

    A new Brain is built per message, which is correct (it keeps state small
    and survives crashes), but it used to mean a new blank CONVERSATION per
    message too. Passing the channel gives the Brain somewhere to load the
    last few turns from, so "yes, proceed" still knows what it is proceeding
    with. Each door keeps its own thread: a phone call does not inherit the
    middle of a Telegram exchange."""
    session = Session(transport)
    channel = f"chan:{transport.value}"
    b = brain.Brain(session, jid=jid, emit=emit, channel=channel)
    emit({"type": "heard", "text": text, "via": transport.value})
    return await asyncio.to_thread(b.run, text)


# ─────────────────────────────────────────────────────────────────────────────
# Telegram — long polling, so it works when the tunnel is down
# ─────────────────────────────────────────────────────────────────────────────

async def telegram_loop() -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not (token and chat_id):
        print("  [telegram] not configured - skipping")
        return
    api = f"https://api.telegram.org/bot{token}"
    offset = 0
    print(f"  [telegram] listening (chat {chat_id})")

    async with httpx.AsyncClient(timeout=40.0) as client:
        while True:
            try:
                r = await client.get(f"{api}/getUpdates",
                                     params={"offset": offset, "timeout": 30})
                for upd in (r.json().get("result") or []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message") or {}
                    # ONLY the owner is ever answered, and silently otherwise.
                    if str((msg.get("chat") or {}).get("id")) != chat_id:
                        continue
                    text = (msg.get("text") or "").strip()
                    if not text:
                        continue
                    if text.lower() in ("stop", "/stop", "cancel"):
                        # "Stopped." used to be a WORD and nothing else. It now
                        # cancels every running task and reports how many
                        # actually stopped, because a stop button that only
                        # changes a label is worse than no stop button.
                        live = tasks.running()
                        for row in live:
                            tasks.cancel(int(row["id"]), reason="he said stop")
                        note = (f"Stopped {len(live)} running task"
                                f"{'s' if len(live) != 1 else ''}."
                                if live else "Nothing was running.")
                        await client.post(f"{api}/sendMessage", json={
                            "chat_id": chat_id, "text": note})
                        continue
                    # "Jarvis is typing..." while it works. A turn can take
                    # ten seconds, and ten seconds of nothing looks broken -
                    # he asked twice whether it had heard him at all. Telegram
                    # clears the indicator after ~5s, so it is refreshed until
                    # the reply is ready rather than sent once and forgotten.
                    async def keep_typing() -> None:
                        try:
                            while True:
                                await client.post(f"{api}/sendChatAction", json={
                                    "chat_id": chat_id, "action": "typing"})
                                await asyncio.sleep(4)
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            pass

                    typing = asyncio.create_task(keep_typing())
                    try:
                        reply = await handle(text, Transport.TELEGRAM)
                    finally:
                        typing.cancel()
                    await client.post(f"{api}/sendMessage", json={
                        "chat_id": chat_id, "text": reply[:4000]})
            except Exception as e:
                print(f"  [telegram] {type(e).__name__}: {e}")
                await asyncio.sleep(3)


# ─────────────────────────────────────────────────────────────────────────────
# Phone — Twilio in, realtime.py out. realtime.py is untouched.
# ─────────────────────────────────────────────────────────────────────────────

MAX_RECONNECTS = 3

VOICE_STYLE = """
HOW YOU SOUND
Speak in a low, deep register with a steady, level delivery. Calm and grounded, unhurried, slightly clipped - a composed man who knows what he is doing. Do NOT sound bright, chirpy, breathy, apologetic or eager to please. No upward inflection at the end of sentences; finish them flat and definite. Keep the pace measured. Never gush, never over-apologise, never perform enthusiasm."""


PHONE_PROMPT = """You are Jarvis, on a phone call with your owner. You are not ChatGPT or any other product; if asked, you are Jarvis.

You can drive his computer from this call - run_python works here exactly as it does at the desk. Open things, read files, check what is actually happening, and do what he asks.

Anything that waits is a job: say you have scheduled it and give the number. Never say you are monitoring something unless you just scheduled it.

Say what tools returned, never what you intended. If something failed, say it failed. He cannot see a screen, so a confident wrong answer is worse here than anywhere.

Short sentences. No markdown. Numbers as a person would say them.""" + VOICE_STYLE


def public_host() -> str:
    """The tunnel hostname, re-read from .env ON EVERY CALL.

    THIS IS WHY CALLS SAID "an application error has occurred".

    A Cloudflare quick tunnel gets a NEW random hostname every time it starts.
    tunnel.py writes the new one into .env and repoints Twilio - but main.py
    had already read PUBLIC_HOST into its environment at import time and kept
    serving the OLD one. So /voice returned valid TwiML naming a hostname that
    no longer resolved: the call connected, the media stream dialled a dead
    address, and Twilio played the error recording. His phone rang and then
    apologised to him.

    Reading the file per request costs microseconds and makes a running Jarvis
    survive a tunnel restart without one. The environment is only a fallback,
    because .env is the thing tunnel.py actually updates."""
    host = ""
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("PUBLIC_HOST="):
                host = line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    host = host or (os.environ.get("PUBLIC_HOST") or "")
    return host.replace("https://", "").replace("http://", "").rstrip("/")


def _twiml(body: str) -> PlainTextResponse:
    return PlainTextResponse(
        f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>',
        media_type="text/xml")


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))[:300]


@router.post("/voice")
async def voice(request: Request, From: str = Form(default=""),
                To: str = Form(default="")) -> PlainTextResponse:
    """Twilio hits this. The allowlist runs BEFORE any audio is connected."""
    mine = (os.environ.get("MY_NUMBER") or "").strip()
    ours = (os.environ.get("TWILIO_NUMBER") or "").strip()
    caller = (From or "").strip()
    if not mine:
        return _twiml("<Say>Jarvis has no owner number configured.</Say>")

    # An outbound call reverses the fields: From is ours, To is his.
    outbound = request.query_params.get("outbound") == "1"
    reason = (request.query_params.get("reason") or "").strip()
    if outbound:
        if (To or "").strip() != mine or (ours and caller != ours):
            return _twiml("<Say>This number is not available.</Say><Reject/>")
    elif caller != mine:
        return _twiml("<Say>This number is not available.</Say><Reject/>")

    host = public_host()
    if not host or "example.com" in host:
        return _twiml("<Say>Jarvis has no public address configured.</Say>")

    param = (f'<Parameter name="reason" value="{_xml_escape(reason)}" />'
             if outbound and reason else "")
    return _twiml(f'<Connect><Stream url="wss://{host}/stream">{param}</Stream>'
                  f'</Connect>')


# An explicit instruction. Unambiguous wherever it appears in the sentence.
HANGUP_COMMAND = re.compile(
    r"(?:cut|end|drop|stop|disconnect)\s+(?:the\s+|this\s+)?call"
    r"|hang\s?up", re.I)

# A goodbye. Only counts as one when it ENDS a short utterance, because
# "tell him goodbye tomorrow is fine" and "remind me to say bye to Uma" are
# instructions ABOUT a farewell, not the Boss ending his own call. Hanging up
# on him mid-sentence because he used the word is worse than not hanging up.
HANGUP_FAREWELL = re.compile(
    r"(?:good\s?bye|bye[- ]bye|bye|that(?:'s| is)\s+all(?:\s+for\s+now)?)"
    r"\s*[.!]*\s*$", re.I)
MAX_FAREWELL_WORDS = 6


def _wants_hangup(said: str) -> bool:
    """Did he just tell Jarvis to end the call?

    Checked in code, not left to the model. Told to hang up, a model tends to
    SAY it is hanging up and then keep the line open - which is exactly the
    class of claim this build refuses to accept from wording alone."""
    text = (said or "").strip()
    if not text:
        return False
    if HANGUP_COMMAND.search(text):
        return True
    return (len(text.split()) <= MAX_FAREWELL_WORDS
            and bool(HANGUP_FAREWELL.search(text)))


@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Twilio media <-> realtime.py. The relay loop NEVER blocks.

    Awaiting the session setup here once cost a call: three seconds inside the
    `start` branch while Twilio pushed fifty packets a second, and Twilio
    dropped the stream. Setup runs beside the loop; audio is buffered."""
    from collections import deque

    await ws.accept()
    session = Session(Transport.PHONE)
    stream_sid = None
    rs = None
    runner = setup = None
    ready = False
    reconnects = 0
    hangup = False
    buffered: deque[str] = deque(maxlen=500)

    async def to_twilio(b64: str) -> None:
        if stream_sid is None:
            return
        try:
            await ws.send_text(json.dumps({"event": "media",
                                           "streamSid": stream_sid,
                                           "media": {"payload": b64}}))
        except Exception:
            pass

    async def emit_ev(ev: dict) -> None:
        # HANG UP WHEN TOLD TO. Saying "cut the call" used to do nothing at
        # all: the model would politely agree to end it and then keep the line
        # open, because nothing was listening for the instruction. The
        # transcript is already flowing past here, so it is checked here.
        nonlocal hangup
        if ev.get("type") == "transcript" and _wants_hangup(ev.get("text", "")):
            hangup = True
            emit({"type": "call", "state": "ending"})
        emit(ev)

    def bring_up(greeting: str) -> None:
        nonlocal setup, ready
        ready = False

        async def work() -> None:
            nonlocal rs, runner, ready
            try:
                ctx = await asyncio.to_thread(state.facts_block)
                rs = realtime.RealtimeSession(
                    session, instructions=PHONE_PROMPT + ctx,
                    emit=emit_ev, on_audio=to_twilio, greeting=greeting,
                    input_format=realtime.FORMAT_MULAW,
                    output_format=realtime.FORMAT_MULAW)
                await rs.connect()
                runner = asyncio.create_task(rs.run())
                while buffered:
                    await rs.send_audio(buffered.popleft())
                ready = True
            except Exception as e:
                emit({"type": "error", "error": f"call setup: {e}"})

        setup = asyncio.create_task(work())

    try:
        async for raw in ws.iter_text():
            msg = json.loads(raw)
            ev = msg.get("event")
            if ev == "start":
                st = msg.get("start") or {}
                stream_sid = st.get("streamSid")
                reason = (st.get("customParameters") or {}).get("reason", "")
                # JARVIS_GREETING, not a string baked in here. It was set in
                # .env and then ignored on every call, because this line had
                # its own copy - so changing the greeting changed nothing.
                greeting = (f"Boss, {reason.rstrip('.')}. What do you want me to do?"
                            if reason else realtime.GREETING)
                emit({"type": "call", "state": "connected"})
                bring_up(greeting)
            elif ev == "media":
                payload = (msg.get("media") or {}).get("payload", "")
                if rs is None or not ready:
                    buffered.append(payload)
                    continue
                if not rs.alive or not await rs.send_audio(payload):
                    buffered.append(payload)
                    if reconnects >= MAX_RECONNECTS:
                        break
                    reconnects += 1
                    bring_up("")
            elif ev == "stop":
                break
            if hangup:
                # Let the acknowledgement finish before the line drops - being
                # cut off mid-word reads as a crash rather than a goodbye.
                await asyncio.sleep(1.6)
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        emit({"type": "error", "error": f"call: {type(e).__name__}: {e}"})
    finally:
        for t in (runner, setup):
            if t is not None:
                t.cancel()
        if rs is not None:
            await rs.close()
        emit({"type": "call", "state": "ended"})
        try:
            await ws.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# The desk — the ONLY place run_python can be reached from
#
# This had to be rebuilt. It used to live in desk.py behind the wake word, the
# microphone and the tool loop all at once, which is why closing the window
# took Jarvis down with it. Here the desk is a door like the other two: it
# turns input into a Session(Transport.DESK) and hands it to Brain.
#
# It used to matter more than the other doors, because run_python was denied
# on phone and Telegram. That restriction is gone: all three transports run
# code now, so this is simply the third door rather than the privileged one.
# ─────────────────────────────────────────────────────────────────────────────

async def desk_console() -> None:
    """Type at the window main.py is running in. No dependencies, always works."""
    print("  [desk] type here to talk to jarvis (blank line = nothing)")
    while True:
        try:
            text = (await asyncio.to_thread(input, "you> ")).strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not text:
            continue
        if text.lower() in ("stop", "quit", "exit"):
            print("  [desk] stopped.")
            continue
        try:
            print(f"jarvis> {await handle(text, Transport.DESK)}\n")
        except Exception as e:
            print(f"  [desk] {type(e).__name__}: {e}")


DESK_PROMPT = """You are Jarvis, at your owner's desk. You are not ChatGPT or any other product; if asked, you are Jarvis.

This is the one place you can run code. Use run_python for anything real - reading files, driving Chrome, checking what is actually there. Look before you answer.

Anything that waits is a job: schedule it and say the number. Never say you are monitoring something unless you just scheduled it.

Say what tools returned, never what you intended. If something failed, say it failed.

Short sentences. No markdown, this is spoken aloud.""" + VOICE_STYLE


async def desk_voice() -> None:
    """The desk microphone, straight into realtime.py. `jarvis --voice`.

    PCM16 at 24kHz both ways, which is realtime.py's own default, so nothing
    in it needed changing to serve a microphone instead of a phone line.
    Server-side turn detection decides when you have stopped talking - there is
    no wake word here, so this is opened deliberately rather than left on."""
    import base64

    try:
        import sounddevice as sd
    except ImportError:
        print("  [voice] sounddevice is not installed - typed desk only")
        return

    rate, block = 24000, 480          # 20ms frames
    session = Session(Transport.DESK)
    out = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
    out.start()

    async def to_speaker(b64: str) -> None:
        try:
            out.write(base64.b64decode(b64))
        except Exception:
            pass

    async def emit_ev(ev: dict) -> None:
        emit(ev)

    ctx = await asyncio.to_thread(state.facts_block)
    rs = realtime.RealtimeSession(
        session, instructions=DESK_PROMPT + ctx, emit=emit_ev,
        on_audio=to_speaker, greeting="")
    await rs.connect()
    runner = asyncio.create_task(rs.run())

    loop = asyncio.get_running_loop()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=100)

    def on_mic(indata, frames, t, status) -> None:
        # Called on PortAudio's thread. Never block it and never touch the
        # session from here - hand the bytes over and get out.
        try:
            loop.call_soon_threadsafe(q.put_nowait, bytes(indata))
        except Exception:
            pass

    stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16",
                               blocksize=block, callback=on_mic)
    stream.start()
    print(f"  [voice] desk microphone open ({rate} Hz) - just talk")
    try:
        while rs.alive:
            chunk = await q.get()
            await rs.send_audio(base64.b64encode(chunk).decode())
    except asyncio.CancelledError:
        pass
    finally:
        stream.stop(); stream.close()
        out.stop(); out.close()
        runner.cancel()
        await rs.close()


# ─────────────────────────────────────────────────────────────────────────────
# Staying controllable
#
# Autonomy without a stop button is a runaway. These are the operations every
# surface uses, exposed once, so the HUD, the console and Telegram all stop the
# SAME task the same way instead of each inventing its own idea of "stop".
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/tasks")
async def list_tasks() -> dict:
    live = tasks.running()
    return {"count": len(live),
            "running": [{"id": t["id"], "objective": t["objective"][:120],
                         "state": t["state"].value, "action": t["action"],
                         "attempts": t["attempts"]} for t in live]}


@app.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int) -> dict:
    try:
        t = tasks.cancel(task_id, reason="asked to stop")
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    emit({"type": "task", "id": task_id, "state": t["state"].value})
    return {"ok": True, "id": task_id, "state": t["state"].value}


@app.post("/tasks/{task_id}/pause")
async def pause_task(task_id: int) -> dict:
    try:
        t = tasks.pause(task_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    emit({"type": "task", "id": task_id, "state": t["state"].value})
    return {"ok": True, "id": task_id, "state": t["state"].value}


@app.post("/tasks/{task_id}/resume")
async def resume_task(task_id: int) -> dict:
    try:
        t = tasks.resume(task_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    emit({"type": "task", "id": task_id, "state": t["state"].value})
    return {"ok": True, "id": task_id, "state": t["state"].value}


@app.post("/authenticate")
async def authenticate(pin: str = Form(default="")) -> dict:
    """Open the trusted session. The PIN is never echoed back or logged."""
    r = safety.authenticate(pin)
    return {"ok": r["ok"], "minutes": r.get("trusted_for_minutes"),
            "error": r.get("error")}


@app.get("/errors")
async def recent_errors() -> dict:
    """What has gone wrong lately, so nothing has to be guessed at."""
    rows = state.recent_errors(30)
    return {"count": len(rows),
            "errors": [{"where": r["where_"], "kind": r["kind"],
                        "detail": (r["detail"] or "")[:200],
                        "fatal": bool(r["fatal"])} for r in rows]}


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "jobs": len(state.jobs("pending")),
            "spend_today": round(state.spend_today(), 4)}


app.include_router(router)


# ─────────────────────────────────────────────────────────────────────────────
# The tunnel. Jarvis owns it, because nobody else was going to.
# ─────────────────────────────────────────────────────────────────────────────

TUNNEL_WATCH_S = 120
TUNNEL_DEEP_EVERY = 5          # a full public check every 5th cycle (~10 min)


async def tunnel_keeper() -> None:
    """Start the tunnel, repoint Twilio, and keep doing it for as long as
    Jarvis runs.

    THE PHONE DID NOT WORK, AND THIS IS THE WHOLE REASON. Every piece needed
    already existed in `tunnel.py` - `start`, `alive`, `write_env`,
    `point_twilio` - and NOTHING CALLED ANY OF IT. `alive()` was written for a
    watchdog, and its own docstring describes the symptom ("sometimes the call
    isn't connecting"), but the watchdog it was written for did not exist in
    this tree. So the phone worked only if the owner happened to have run
    `python tunnel.py up` in another window, and died silently the moment that
    window closed - which is exactly the state it was found in: a pid file
    naming a dead process, a `PUBLIC_HOST` that no longer resolved, and Twilio
    still pointed at it. `tunnel.py check` even printed "Restart jarvis - it
    starts a fresh tunnel and repoints Twilio", which was FALSE advice: main.py
    did not import tunnel at all.

    A quick tunnel takes a NEW random hostname every launch, so starting one is
    only half the job - `.env` and Twilio both have to be told, or the call
    connects and dials an address that is gone. All three steps happen here,
    in that order, every time.

    The checks are deliberately two speeds. `alive()` is a PID check, so it is
    free and runs every cycle; a dead process is the common cause. Every fifth
    cycle does the expensive one - resolve the public hostname, fetch the real
    webhook, and confirm Twilio still points at it - because cloudflared can
    keep running while the tunnel behind it has rotted, and that failure is
    invisible to a PID check.

    `--no-tunnel` opts out, for working offline or when a tunnel is already
    being managed by hand."""
    if "--no-tunnel" in sys.argv:
        print("  [tunnel] --no-tunnel: the phone will not work this session")
        return

    async def establish(why: str) -> None:
        res = await asyncio.to_thread(tunnel.start, LOCAL_PORT)
        if not res.get("ok"):
            print(f"  [tunnel] {why}: could not start - {res.get('error')}")
            state.record_error("tunnel.start", str(res.get("error")))
            return
        url = res["url"]
        await asyncio.to_thread(tunnel.write_env, url)
        print(f"  [tunnel] {why}: {url}")
        pt = await asyncio.to_thread(tunnel.point_twilio, url)
        if pt.get("ok"):
            print(f"  [tunnel] twilio {pt['number']} -> {pt['voice_url']}")
        else:
            # Worth shouting about: the tunnel is up, so /health looks fine,
            # but every incoming call still lands on the old dead hostname.
            print(f"  [tunnel] TWILIO NOT REPOINTED - calls will still fail: "
                  f"{pt.get('error')}")
            state.record_error("tunnel.point_twilio", str(pt.get("error")))

    cycle = 0
    while True:
        try:
            if not await asyncio.to_thread(tunnel.alive):
                await establish("up" if cycle == 0 else "restarted")
            elif cycle % TUNNEL_DEEP_EVERY == 0:
                # public_host() re-reads .env, which is the file write_env()
                # updates - so this sees the CURRENT hostname, not the one
                # this process happened to import.
                host = public_host()
                v = await asyncio.to_thread(tunnel.verify, f"https://{host}") \
                    if host else {"ok": False, "reason": "no host in .env"}
                if not v.get("ok"):
                    print(f"  [tunnel] public check failed "
                          f"({v.get('reason')}) - rebuilding")
                    await asyncio.to_thread(tunnel.stop)
                    await establish("rebuilt")
        except asyncio.CancelledError:
            raise
        except Exception as e:                    # never let this kill Jarvis
            state.record_error("tunnel_keeper", e)
        cycle += 1
        await asyncio.sleep(TUNNEL_WATCH_S)


# ─────────────────────────────────────────────────────────────────────────────

async def run() -> None:
    global _loop
    _loop = asyncio.get_running_loop()

    import uvicorn
    config = uvicorn.Config(app, host="127.0.0.1", port=LOCAL_PORT,
                            log_level="warning")
    server = uvicorn.Server(config)

    # Anything left mid-flight by a crash gets picked up again or failed
    # honestly. Without this a killed process leaves rows claiming to be
    # EXECUTING forever, which is both not running and not finished.
    # GUARDED, because it used to be fatal. An IllegalTransition raised in
    # here took down the entire process on boot - no server, no Telegram, no
    # tunnel, no phone - over a single odd row in a housekeeping sweep. The
    # task list is worth tidying; it is not worth the machine. Whatever goes
    # wrong is recorded and Jarvis still comes up.
    try:
        revived = tasks.recover_interrupted()
        if revived:
            print(f"  recovered {len(revived)} interrupted task(s): {revived}")
    except Exception as e:
        state.record_error("startup.recover_interrupted", e)
        print(f"  [startup] task recovery failed, carrying on: "
              f"{type(e).__name__}: {e}")

    print(f"\n  jarvis up. http://127.0.0.1:{LOCAL_PORT}  db={state.DB_PATH}")
    print(f"  worker: run `python worker.py` in another window\n")

    # Named `runners`, not `tasks`: a local called `tasks` shadowed the tasks
    # MODULE for the whole function, so recover_interrupted() above raised
    # UnboundLocalError and the backend died on startup. Caught by actually
    # starting it - importing main.py was perfectly happy.
    runners = [asyncio.create_task(server.serve()),
               asyncio.create_task(telegram_loop()),
               asyncio.create_task(tunnel_keeper())]
    if "--no-desk" not in sys.argv:
        runners.append(asyncio.create_task(desk_console()))
    if "--voice" in sys.argv:
        runners.append(asyncio.create_task(desk_voice()))
    await asyncio.gather(*runners)


def main() -> int:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nshutting down.")
    finally:
        # Take the tunnel down with us. Left running, it becomes an orphaned
        # cloudflared plus a pid file naming it - and the NEXT start reads that
        # file, believes a tunnel is alive, and never makes one. That is how
        # this was found broken: a pid file pointing at a process that had
        # been gone for hours.
        if "--no-tunnel" not in sys.argv:
            try:
                if tunnel.stop():
                    print("  [tunnel] stopped")
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
