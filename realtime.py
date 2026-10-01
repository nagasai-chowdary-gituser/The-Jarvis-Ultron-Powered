"""
realtime.py — speech-to-speech engine, shared by desk and phone.

ONE implementation for both transports (AGENTS.md §13). Desk feeds it 16 kHz PCM
from the microphone; phone feeds it 8 kHz mu-law straight off Twilio. The only
difference is the audio format declared at session start and where the output
bytes are written. Tool calls go through brain.dispatch_tool() exactly as the
chained path does, so the transport gate, preflight and audit log all still apply.

WHY THIS EXISTS: the chained path (STT -> LLM -> TTS) measured ~11 s to first
sound, because it is strictly sequential and every stage pays its own round trip.
Streaming helps but cannot fix the floor: tts-1's time-to-first-byte is ~1.3-1.9 s
no matter how short the text. Measured against the GA Realtime API, first audio
arrives in 0.76 s. Six times faster for about 2.3x the cost.

GA API FACTS — verified by live connection on 2026-09-15, not from memory:
  - URL wss://api.openai.com/v1/realtime?model=...
  - The `OpenAI-Beta: realtime=v1` header is DEAD. Sending it returns
    beta_api_shape_disabled: "The Realtime Beta API is no longer supported."
    Send ONLY Authorization.
  - The session schema is NESTED, not flat. It is
        session.audio.input.format  = {"type": "audio/pcm", "rate": 24000}
        session.audio.input.turn_detection = {"type": "server_vad", ...}
        session.audio.output.voice  = "cedar"
        session.output_modalities   = ["audio"]
    NOT the old input_audio_format: "pcm16".
  - Audio events are response.output_audio.delta /
    response.output_audio_transcript.delta, not response.audio.delta.
  - Measured output rate: 20 audio tokens per second of speech.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from typing import Any, Awaitable, Callable

import websockets

import brain
import models
import state
from models import PRICING
from safety import Session

WS_URL = "wss://api.openai.com/v1/realtime?model={model}"

# Audio format blocks. Twilio speaks g711 mu-law at 8 kHz and the API accepts it
# natively — AGENTS.md §10: relay those payloads unchanged, never resample.
# NOTE: 24000 is the MINIMUM the API accepts for audio/pcm. Passing 16000 (the
# rate the wake-word model wants) is rejected with integer_below_min_value and
# leaves the session on its toolless default — see connect().
FORMAT_PCM16 = {"type": "audio/pcm", "rate": 24000}
FORMAT_MULAW = {"type": "audio/pcmu"}
MIN_PCM_RATE = 24000

# "ash" is the deep, level male voice of the GA set - cedar is warmer and reads
# as friendly rather than formal, which is the opposite of what Jarvis is for.
# Overridable without touching code: the realtime voices are alloy, ash, ballad,
# coral, echo, sage, shimmer, verse, marin and cedar.
VOICE = (os.environ.get("JARVIS_VOICE") or "ash").strip()

# Spoken once when a session opens, before the user says anything. Silence on
# startup gives you no way to tell a listening Jarvis from a broken one.
GREETING = (os.environ.get("JARVIS_GREETING")
            or "Welcome back, Boss. What do you want me to do?")


def greeting_for(transport=None) -> str:
    """Say hello the moment the line opens.

    The PIN branch is gone with the PIN system. There is no locked state to
    greet any more: phone and Telegram can do everything except run code, and
    that is a transport boundary rather than something you unlock."""
    return GREETING


# How long a pause ends your turn. See the note in connect() - 500ms splits
# natural sentences, 700ms does not.
TURN_SILENCE_MS = 700

# A response claiming to be "in progress" longer than this is a lost event, not
# real generation. Realtime answers start in under a second; 20s is far past any
# legitimate case, including a slow tool call.
RESPONSE_STALE_S = 20.0

# Realtime sessions expire after 60 minutes. Without reconnection the socket
# simply closes and the agent is dead while the interface still reads IDLE.
SESSION_MAX_S = 45 * 60


class RealtimeSession:
    """One live speech-to-speech conversation."""

    def __init__(
        self,
        session: Session,
        *,
        instructions: str,
        emit: Callable[[dict], Awaitable[None]] | None = None,
        confirm: Callable[[dict], Awaitable[bool]] | None = None,
        on_audio: Callable[[str], Awaitable[None]] | None = None,
        input_format: dict | None = None,
        output_format: dict | None = None,
        model: str | None = None,
        greeting: str = "",
    ):
        self.session = session
        self.instructions = instructions
        self.greeting = greeting
        self.emit = emit or self._noop
        self.confirm = confirm or self._decline
        self.on_audio = on_audio or self._noop_audio
        self.input_format = input_format or FORMAT_PCM16
        self.output_format = output_format or FORMAT_PCM16
        self.model = model or models.REALTIME
        self.ws: Any = None
        self.speaking = False
        self._turn_started = 0.0
        self._first_audio_reported = False
        # call_ids already dispatched. response.function_call_arguments.done and
        # response.done both report the same call, so without this the tool runs
        # twice for one question.
        self._done_calls: set[str] = set()
        # The API allows exactly one response in flight. Sending response.create
        # while one is active returns conversation_already_has_active_response
        # and the turn is lost. Observed live when the user spoke again while
        # Jarvis was still answering.
        self._response_active = False
        self._response_since = 0.0
        self.alive = True
        self.closed_reason = ""
        self.started_at = time.monotonic()
        self._fail_counts: dict[str, int] = {}
        # Both sides of the conversation, kept for the commitment sweep at the
        # end of the call. Capped, because a long call should not grow without
        # bound in memory and the sweep only reads the last stretch anyway.
        self._transcript: list[str] = []

    @staticmethod
    async def _noop(_: dict) -> None:
        return None

    @staticmethod
    async def _noop_audio(_: str) -> None:
        return None

    @staticmethod
    async def _decline(_: dict) -> bool:
        return False

    # ── connection ───────────────────────────────────────────────────────────

    async def connect(self) -> None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        # Authorization ONLY. The beta header now hard-fails the connection.
        self.ws = await websockets.connect(
            WS_URL.format(model=self.model),
            extra_headers={"Authorization": f"Bearer {key}"},
            max_size=32 * 1024 * 1024,
            ping_interval=20,
        )
        await self._recv()                      # session.created
        await self.ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "instructions": self.instructions,
                "output_modalities": ["audio"],
                "tools": self._tools(),
                "tool_choice": "auto",
                "audio": {
                    "input": {
                        "format": self.input_format,
                        # server_vad means OpenAI decides when the user stopped
                        # talking. It also stops us paying for silence on an open
                        # line (AGENTS.md §9) and removes our own 0.9 s hangover.
                        # silence_duration_ms is a real tradeoff. At 500ms a
                        # normal mid-sentence pause ends the turn: observed live,
                        # "WhatsApp is already open. Check how many messages I
                        # have not read" was split in two, and the capture
                        # fired on the first half before the question arrived.
                        # 700ms costs 200ms of latency and avoids burning a whole
                        # turn on a fragment, which is far more expensive.
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.5,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": TURN_SILENCE_MS,
                        },
                        "transcription": {"model": "whisper-1"},
                    },
                    "output": {"format": self.output_format, "voice": VOICE},
                },
            },
        }))
        # VERIFY THE CONFIG APPLIED. This is not optional defensiveness.
        #
        # A rejected session.update does NOT close the connection — it returns an
        # error event and leaves OpenAI's DEFAULT session in place: tools: [],
        # plus the stock "you are a helpful, witty AI that can't do human things
        # in the real world" persona. The result looks like it is working. It
        # talks, it answers, and it quietly cannot call a single tool.
        #
        # Observed live: input format rate 16000 was rejected (minimum is 24000),
        # and Jarvis spent the session telling the user to open YouTube himself.
        # A silent downgrade from agent to chatbot must be a hard failure.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            m = await self._recv()
            t = m.get("type")
            if t == "session.updated":
                applied = m.get("session", {}) or {}
                n_tools = len(applied.get("tools") or [])
                if n_tools == 0:
                    raise RuntimeError(
                        "Realtime session accepted the update but registered 0 "
                        "tools. Jarvis would be a chatbot with no ability to act.")
                await self.emit({"type": "state", "state": "idle",
                                 "model": self.model, "effort": "realtime"})
                await self.emit({"type": "session_ready", "tools": n_tools})
                await self._speak_greeting()
                return
            if t == "error":
                err = m.get("error", {})
                raise RuntimeError(
                    f"Realtime session.update REJECTED: "
                    f"{err.get('message', err)} (param={err.get('param')}). "
                    "Refusing to continue with the default session, which has "
                    "no tools.")
        raise RuntimeError("no session.updated received within 15s")

    async def _on_user_said(self, said: str) -> None:
        """Everything the text agent does on a new turn, done here too.

        THE THREE CHANNELS MUST NOT HAVE SEPARATE BRAINS, and they did. The
        dispatcher, the guardrails, memory and verification were already shared,
        but PLANNING and TASK CONTROL lived in agent.py - which the voice and
        the phone do not use. So a multi-step request typed into Telegram got a
        plan and the same request spoken into a phone got none, and "stop" only
        worked as text."""
        if not said:
            return

        # STOP IS INTERCEPTED, not left to the model to notice and honour.
        # "stop on any transport aborts immediately and asks nothing."
        if said.strip().lower().rstrip("!.") in ("stop", "stop it", "wait",
                                                 "hold on", "pause", "cancel"):
            await self.cancel()
            await self.emit({"type": "state", "state": "idle"})
            return

        # PLANNING AND TASK STATE ARE GONE FROM HERE. They were three modules
        # and a background model call to decide the ORDER of steps - and the
        # order is now the model's own problem, which is what it is for. What
        # replaces them is schedule_job: anything that waits leaves this loop
        # entirely and becomes a row the worker picks up.

    async def _speak_greeting(self) -> None:
        """Say hello the moment the session is live, before any input.

        SAFE HERE, AND ONLY HERE. An extra response.create while one is already
        running returns conversation_already_has_active_response and - worse -
        the second response carries the same tools, so it re-runs whatever tool
        the first one called. That is why there is no spoken filler around tool
        calls. At this point the session has just been created and nothing is in
        flight, so exactly one response can be requested safely.

        `instructions` is scoped to this response only; it does not replace the
        session prompt."""
        if not self.greeting:
            return
        self._mark_response(True)
        # CHECK THE RESULT, do not wait for an exception. _send swallows the
        # failure now, so the old `except` never fires - and a latched
        # _response_active makes _request_response() return silently forever,
        # which looks exactly like Jarvis idling and never speaking again.
        if not await self._send({
                "type": "response.create",
                "response": {
                    "instructions": (
                        f"Greet the user, out loud, with exactly this and "
                        f"nothing more: \"{self.greeting}\""),
                },
        }):
            self._mark_response(False)
            return
        await self.emit({"type": "state", "state": "speaking"})

    async def _recv(self) -> dict:
        return json.loads(await self.ws.recv())

    def _tools(self) -> list[dict]:
        """Realtime tool shape is flat, like the Responses API."""
        # THE SAME FOUR the text path uses. Realtime's shape is already
        # flat, so they pass straight through.
        return brain.TOOLS

    # ── input ────────────────────────────────────────────────────────────────

    async def send_audio(self, b64_payload: str) -> bool:
        """Append one chunk of caller/mic audio. True if it went out.

        RETURNS A RESULT INSTEAD OF RAISING, and that is the whole fix for calls
        dropping mid-sentence.

        This used to `await self.ws.send(...)` bare. When the OpenAI socket
        closed, websockets raised ConnectionClosed straight through the caller's
        relay loop, past its per-event handling, into the outer except - which
        hung up on the owner. Observed live: greeting, one reply, "Hello.", dead.

        `alive` could not prevent it either, because `alive` is set by run()
        when IT notices the close, and Twilio pushes audio every 20ms - so the
        send always reaches the dead socket first. The flag lags; this does not.
        The caller reconnects on a False and the call continues."""
        if self.ws is None:
            return False
        try:
            await self.ws.send(json.dumps({
                "type": "input_audio_buffer.append", "audio": b64_payload}))
            return True
        except Exception as e:
            # Mark it here rather than waiting for run() to work it out, so the
            # very next event takes the reconnect path.
            self.alive = False
            self.closed_reason = self.closed_reason or f"send failed: {type(e).__name__}"
            return False

    async def _send(self, payload: dict) -> bool:
        """Every write to the socket goes through here. Never raises.

        ONE RAW `await self.ws.send(...)` IS ENOUGH TO END A CALL. It raises
        ConnectionClosed straight through whatever called it, and on the phone
        that lands in the handler that hangs up on the owner - which is how a
        conversation died mid-sentence twice over. send_audio was fixed first
        and it was not enough, because send_text, cancel and the tool-output
        write were all still bare, and every one of them is reachable while a
        call is live: a tapped PIN, a barge-in, any tool at all.

        The session is marked dead here too, so the very next event takes the
        reconnect path rather than discovering the same corpse again."""
        if self.ws is None:
            return False
        try:
            await self.ws.send(json.dumps(payload))
            return True
        except Exception as e:
            self.alive = False
            self.closed_reason = self.closed_reason or f"send failed: {type(e).__name__}"
            return False

    async def send_text(self, text: str) -> None:
        if await self._send({
                "type": "conversation.item.create",
                "item": {"type": "message", "role": "user",
                         "content": [{"type": "input_text", "text": text}]}}):
            await self._request_response()

    async def cancel(self) -> None:
        """Barge-in: stop the model mid-sentence when the user starts talking."""
        if await self._send({"type": "response.cancel"}):
            self._mark_response(False)

    # ── the relay loop ───────────────────────────────────────────────────────

    async def run(self) -> None:
        """Consume server events until the socket closes."""
        pending: dict[str, dict] = {}
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                t = ev.get("type", "")

                if t == "input_audio_buffer.speech_started":
                    self.session.new_turn()     # fresh per-turn tool budget
                    self._turn_started = time.monotonic()
                    self._first_audio_reported = False
                    if self.speaking:
                        await self.emit({"type": "barge_in"})
                        await self.cancel()
                    await self.emit({"type": "state", "state": "listening"})

                elif t == "input_audio_buffer.speech_stopped":
                    await self.emit({"type": "state", "state": "thinking"})

                elif t == "conversation.item.input_audio_transcription.completed":
                    said = ev.get("transcript", "").strip()
                    await self.emit({"type": "transcript", "text": said})
                    self._remember_line("You", said)
                    await self._on_user_said(said)

                elif t == "response.output_audio.delta":
                    if not self._first_audio_reported:
                        self._first_audio_reported = True
                        self.speaking = True
                        dt = (time.monotonic() - self._turn_started
                              if self._turn_started else 0.0)
                        await self.emit({"type": "state", "state": "speaking",
                                         "latency_s": round(dt, 2)})
                    await self.on_audio(ev.get("delta", ""))

                elif t == "response.output_audio.done":
                    self.speaking = False

                elif t == "response.output_audio_transcript.done":
                    reply = ev.get("transcript", "").strip()
                    self._remember_line("Jarvis", reply)
                    await self.emit({"type": "reply", "text": reply})

                elif t == "response.function_call_arguments.done":
                    pending[ev.get("call_id", "")] = {
                        "name": ev.get("name", ""),
                        "arguments": ev.get("arguments", "{}"),
                    }

                elif t == "response.created":
                    self._mark_response(True)

                elif t == "response.done":
                    self._mark_response(False)
                    await self._log_usage(ev)
                    resp = ev.get("response", {})
                    calls = [o for o in resp.get("output", [])
                             if o.get("type") == "function_call"]
                    for c in calls:
                        pending.setdefault(c.get("call_id", ""), {
                            "name": c.get("name", ""),
                            "arguments": c.get("arguments", "{}")})
                    if pending:
                        await self._run_tools(pending)
                        pending = {}
                    else:
                        self.speaking = False
                        await self.emit({"type": "state", "state": "idle"})

                elif t == "error":
                    err = ev.get("error", {}) or {}
                    # If the server says a response is already running, believe it
                    # and mark our flag, or we keep retrying and keep colliding.
                    if err.get("code") == "conversation_already_has_active_response":
                        self._mark_response(True)
                    else:
                        await self.emit({"type": "error",
                                         "error": str(err)[:300]})
        except websockets.ConnectionClosed as e:
            # Do NOT report idle here. Idle means "ready and waiting", and a
            # closed socket is the opposite: the agent is gone. Reporting idle is
            # why a dead session looked like a working one.
            self.closed_reason = f"connection closed ({e.code})"
            await self.emit({"type": "state", "state": "error"})
        except Exception as e:
            self.closed_reason = f"{type(e).__name__}: {e}"
            await self.emit({"type": "state", "state": "error"})
        else:
            # `async for` ending without an exception means the peer closed
            # cleanly — typically the 60-minute session expiry. Record it so the
            # reconnect log says why rather than just "session ended".
            self.closed_reason = self.closed_reason or "server closed the session"
        finally:
            self.alive = False

    # ── tools ────────────────────────────────────────────────────────────────

    async def _run_tools(self, pending: dict[str, dict]) -> None:
        """Dispatch through the SAME brain as every other transport.

        Latency note: a slow tool means silence, which on a phone reads as a
        dropped call (AGENTS.md §10). Speak a filler the moment a dispatch
        starts, before the work happens."""
        await self.emit({"type": "state", "state": "acting"})

        # ONE BRAIN, FOUR TOOLS. This used to read the calls, group the
        # read-only ones, run them in parallel through a 39-tool dispatcher,
        # then classify any failure through a recovery module. All of that was
        # scaffolding around a tool list that no longer exists: there are four
        # tools now, only one of them touches the machine, and it is denied on
        # this transport anyway.
        for call_id, call in list(pending.items()):
            if call_id in self._done_calls:
                continue            # response.done and .arguments.done both fire
            self._done_calls.add(call_id)
            name = call["name"]
            try:
                args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}

            await self.emit({"type": "tool_call", "tool": name,
                             "code": args.get("code", ""),
                             "why": args.get("why", "")})

            result = await asyncio.to_thread(self._dispatch, name, args)

            payload = (result.get("stdout") or result.get("spoken")
                       or result.get("note") or result.get("error") or "")
            await self.emit({"type": "tool_result", "tool": name,
                             "ok": bool(result.get("ok")),
                             "detail": str(payload)[:300]})

            await self._send({
                "type": "conversation.item.create",
                "item": {"type": "function_call_output", "call_id": call_id,
                         "output": json.dumps(result, default=str)[:2000]}})

        self._turn_started = time.monotonic()
        self._first_audio_reported = False
        await self._request_response()

    def _dispatch(self, name: str, args: dict) -> dict:
        """Run one tool on a worker thread, through the shared gate.

        A Brain is built per call rather than held, because it carries the
        turn's evidence and that must not leak between turns - evidence from an
        earlier turn is exactly how "I'm monitoring" gets said again."""
        b = brain.Brain(self.session)
        return b.dispatch(name, args)

    def _mark_response(self, active: bool) -> None:
        self._response_active = active
        self._response_since = time.monotonic() if active else 0.0

    def _response_is_stale(self) -> bool:
        """A response that has been 'in progress' for longer than any real one
        takes is a lost event, not a live generation.

        This is the fault behind 'it answered once then went quiet': if
        response.done never arrives — dropped event, cancelled turn, or the
        conversation_already_has_active_response error latching the flag — then
        _request_response() silently returns forever and Jarvis never speaks
        again. It looks exactly like idling."""
        return (self._response_active
                and self._response_since
                and (time.monotonic() - self._response_since) > RESPONSE_STALE_S)

    async def _request_response(self) -> None:
        """Ask for a reply, unless one is genuinely still being generated."""
        if self._response_active and not self._response_is_stale():
            return
        if self._response_is_stale():
            await self.emit({"type": "state", "state": "thinking"})
        self._mark_response(True)
        if not await self._send({"type": "response.create"}):
            self._mark_response(False)

    # ── cost ─────────────────────────────────────────────────────────────────

    async def _log_usage(self, ev: dict) -> None:
        u = (ev.get("response") or {}).get("usage") or {}
        if not u:
            return
        it = u.get("input_token_details", {}) or {}
        ot = u.get("output_token_details", {}) or {}
        r = PRICING.get(self.model)
        usd = 0.0
        if r:
            usd = (
                (it.get("audio_tokens", 0) or 0) * (r.audio_input_per_1m or 0)
                + (it.get("text_tokens", 0) or 0) * (r.input_per_1m or 0)
                + (ot.get("audio_tokens", 0) or 0) * (r.audio_output_per_1m or 0)
                + (ot.get("text_tokens", 0) or 0) * (r.output_per_1m or 0)
            ) / 1_000_000

        # One ledger for every model, in state.spend.
        state.record_spend(
            self.model,
            input_tokens=int(u.get("input_tokens") or 0),
            cached_tokens=int((u.get("input_token_details") or {}).get("cached_tokens") or 0),
            output_tokens=int(u.get("output_tokens") or 0),
            usd=usd)
        await self.emit({"type": "cost", "turn_usd": usd,
                         "today_usd": state.spend_today()})

    def should_recycle(self) -> bool:
        """True when this session is near its 60-minute server-side expiry.

        Recycling early and deliberately is far better than being cut off
        mid-sentence: the user never sees it, where an expiry mid-task looks
        like Jarvis simply stopping."""
        return (time.monotonic() - self.started_at) > SESSION_MAX_S

    # ── commitments ──────────────────────────────────────────────────────────

    TRANSCRIPT_LINES = 80        # a long call; older lines fall off the front
    MIN_LINES_TO_SWEEP = 4       # "hello" / "goodbye" is not worth a model call

    def _remember_line(self, who: str, text: str) -> None:
        """Record one line of the call, in two places with different lifetimes.

        THIS MODULE USED TO TOUCH memory.py NOWHERE AT ALL, which is why every
        call and every desk voice session began as a stranger. The owner:
        "when i call the new llm new chat is talking to me". It was — the socket
        carried the conversation and the socket died with the call.

        The write is fire-and-forget on a thread: a SQLite insert is fast, but
        "fast" on the audio path is still the wrong place to find out that the
        disk is busy."""
        if not text:
            return
        self._transcript.append(f"{who}: {text}")
        del self._transcript[:-self.TRANSCRIPT_LINES]

        role_dir = "in" if who == "You" else "out"
        channel = self.session.transport.value
        threading.Thread(
            target=self._save_turn, args=(role, text, channel),
            daemon=True, name="memory-write").start()

    @staticmethod
    def _save_turn(role: str, text: str, channel: str) -> None:
        try:
            state.add_message(f'self:{channel}', role_dir, text)
        except Exception:
            pass        # never let bookkeeping break a live call

    def _sweep_commitments(self) -> None:
        """Read the finished call and write down what was promised.

        AFTER the call, never during it. Pausing a conversation to take minutes
        of itself costs latency at the one moment it is most visible, and the
        transcript is not complete until the end anyway.

        Runs on a thread, and swallows its failures: bookkeeping must never be
        the reason a call fails to hang up cleanly."""
        if len(self._transcript) < 4:
            return                       # "hello / goodbye" is not worth a call
        text = "\n".join(self._transcript)
        self._transcript = []            # never sweep the same call twice

        def work() -> None:
            try:
                import json as _json

                from openai import OpenAI
                r = OpenAI(timeout=30.0, max_retries=1).responses.create(
                    model=models.BRAIN,
                    instructions=(
                        "List the promises the USER made in this transcript. "
                        'JSON array only: [{"who":"","what":"","due":""}]. '
                        "Only what THEY committed to. Never invent a deadline - "
                        'use "" when none was said. Return [] if none.'),
                    input=[{"role": "user", "content": text[:12000]}],
                    max_output_tokens=600, store=False)
                models.record(r, models.BRAIN)
                raw = (r.output_text or "").strip()
                a, b = raw.find("["), raw.rfind("]")
                for item in _json.loads(raw[a:b + 1]) if a >= 0 < b else []:
                    if str(item.get("what", "")).strip():
                        state.add_commitment(item["what"], item.get("who", ""),
                                             item.get("due", ""), "call")
            except Exception:
                pass

        threading.Thread(target=work, daemon=True, name="sweep").start()

    async def close(self) -> None:
        try:
            self._sweep_commitments()
        except Exception:
            pass
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
