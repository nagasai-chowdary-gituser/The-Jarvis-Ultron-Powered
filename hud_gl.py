"""
hud_gl.py — the ULTRON interface.

THE LOOK, AND WHY IT IS BUILT THIS WAY
    The reference is the JARVIS title card: a near-black screen, one large
    turbulent ring made of thousands of teal particles, the wordmark burning
    through the middle of it, and nothing else that shouts. Everything that is
    not the ring is thin, dim and at the edges.

    That look is not drawable with outlined shapes. It needs two things:

    1. DENSITY, not strokes. The ring is ~9000 individual points scattered into
       a float accumulator. Where points pile up the value rises, and brightness
       follows density - which is what makes it read as luminous dust instead of
       a drawn circle. The accumulator is built in numpy in one vectorised pass;
       drawing 9000 circles a frame in Python would not hold 60fps.

    2. REAL BLOOM, not stacked alpha. The energy layer is rendered at half
       resolution, then added to the screen twice: once scaled up smoothly, and
       once through a tiny intermediate (1/8th), which is a genuine blur.
       Averaging down and back up spreads light the way a lens does.

    THE FAILURE THIS REPLACES: the previous version faked glow by drawing the
    same shape repeatedly with additive alpha. Additive blending accumulates
    without bound, so a filled circle became an opaque disc - 53% of the screen
    went flat cyan. Bloom must come from blurring what is already drawn, never
    from painting more opaque copies of it.

WHAT IS ACTUALLY 3D
    The ring is a real torus: points parameterised by angle-around-the-ring and
    angle-around-the-tube, displaced by four layered sine waves so the band
    ripples like the reference, then rotated by a slow wobble and perspective
    projected. Near-side points are brighter, which is what gives it depth.

EVERY NUMBER ON SCREEN IS REAL
    CPU, memory, disk, spend, latency, the watch readings, the stage lights.
    A dashboard that shows invented values teaches you to stop trusting it,
    which is worse than having no dashboard.
"""
from __future__ import annotations

import math
import os
import queue
import time
from dataclasses import dataclass, field

import numpy as np
import psutil
import pygame

# ── palette ──────────────────────────────────────────────────────────────────
# Teal, sampled from the reference - not the old electric cyan.
BG = (2, 5, 7)
TEAL = (45, 212, 191)
TEAL_HOT = (188, 252, 242)
TEAL_MID = (44, 188, 172)
TEAL_DIM = (15, 92, 88)
TEAL_DEEP = (16, 78, 78)
AMBER = (255, 176, 46)
GREEN = (61, 220, 151)
RED = (255, 77, 94)
TEXT = (184, 232, 228)
TEXT_DIM = (106, 162, 160)
TEXT_FAINT = (68, 116, 118)
WHITE = (228, 252, 248)

STATE_COLOR = {
    "idle": TEAL_MID, "listening": TEAL, "thinking": TEAL,
    "acting": AMBER, "speaking": GREEN, "error": RED,
    "call": RED, "call_thinking": RED, "call_speaking": RED,
}
STATE_LABEL = {
    "idle": "STANDING BY", "listening": "LISTENING", "thinking": "THINKING",
    "acting": "EXECUTING", "speaking": "RESPONDING", "error": "FAULT",
    "call": "ON A CALL", "call_thinking": "ON A CALL", "call_speaking": "ON A CALL",
}
STATE_HINT = {
    "idle": "say something", "listening": "listening",
    "thinking": "working it out", "acting": "doing it",
    "speaking": "answering", "error": "something went wrong",
    "call": "desk microphone locked", "call_thinking": "desk microphone locked",
    "call_speaking": "speaking to caller",
}
STAGES = [("THINK", ("thinking",)), ("PLAN", ("thinking",)),
          ("EXECUTE", ("acting",)), ("DELIVER", ("speaking",))]


@dataclass
class HudState:
    state: str = "idle"
    task: str = ""
    model: str = ""
    level: float = 0.0
    spend: float = 0.0
    latency: float | None = None
    tool: str = ""
    confirm: dict | None = None
    confirm_result: bool | None = None
    on_call: bool = False
    caller: str = ""
    watches: list = field(default_factory=list)
    telegram: bool = False
    phone: bool = False
    devices: list = field(default_factory=list)
    device_request: int | None = None
    running: bool = True

    # THE AUDIT LOG. Every run_python call lands here with its FULL source, as
    # it runs. This is the whole reason the desk is the only place code can
    # run: a boundary you cannot see is not a boundary. Newest first.
    audit: list = field(default_factory=list)
    connected: bool = False

    # THE DESK CHAT. A fourth way in, alongside desk voice, phone and Telegram:
    # type to Jarvis right here on the screen. The HUD is otherwise a display
    # client that only receives; this is the one thing it sends. A typed line
    # goes out through `outbox` (drained by the listen thread, which owns the
    # socket) as {"type":"say"}, and Jarvis's reply arrives back as the same
    # "reply" event every other channel produces, appended to chat_log.
    chat_open: bool = False
    chat_input: str = ""
    chat_log: list = field(default_factory=list)      # (who, text), who in you|jarvis
    outbox: queue.Queue = field(default_factory=queue.Queue)


def _prepare_display() -> None:
    """Declare DPI awareness BEFORE SDL initialises. This must happen first.

    THE HALF-SCREEN BUG. Windows lies about the screen size to any process that
    has not declared DPI awareness. On a display scaled to 150%, a 1920x1080
    monitor is reported as 1280x720, so the window was created at 1280x720 -
    and because pygame.FULLSCREEN is an EXCLUSIVE mode change, Windows did not
    just draw a small window, it switched the monitor to 1280x720. That is why
    every other window on the desktop was rearranged too: the whole display had
    changed resolution underneath them.

    Declaring per-monitor awareness makes Windows report real pixels. The
    exclusive mode change is removed separately, in __init__."""
    os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "0,0")
    os.environ.setdefault("SDL_VIDEO_CENTERED", "0")
    os.environ.setdefault("SDL_WINDOWS_DPI_AWARENESS", "permonitorv2")
    try:
        import ctypes
    except Exception:
        return
    for attempt in (
        lambda: ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)),                  # PER_MONITOR_AWARE_V2, Win10+
        lambda: ctypes.windll.shcore.SetProcessDpiAwareness(2),   # Win8.1+
        lambda: ctypes.windll.user32.SetProcessDPIAware(),        # Vista+
    ):
        try:
            attempt()
            return
        except Exception:
            continue


def _raise_to_front(w: int, h: int) -> None:
    """Cover the whole desktop, taskbar included, and take the foreground.

    Windows refuses SetForegroundWindow from a process that does not already own
    the foreground - the documented workaround is an ALT keypress, which marks
    this process as having had recent input. Without it the window can open
    BEHIND the command prompt, which looks exactly like it never opened."""
    try:
        import ctypes
        u32 = ctypes.windll.user32
        hwnd = pygame.display.get_wm_info().get("window")
        if not hwnd:
            return
        u32.keybd_event(0x12, 0, 0, 0)          # ALT down
        u32.keybd_event(0x12, 0, 2, 0)          # ALT up
        u32.ShowWindow(hwnd, 9)                 # SW_RESTORE
        # HWND_TOP, SWP_SHOWWINDOW. Deliberately NOT topmost: Jarvis opens
        # Chrome and WhatsApp and you have to be able to see them.
        u32.SetWindowPos(hwnd, 0, 0, 0, int(w), int(h), 0x0040)
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
    except Exception:
        pass


class HudGL:
    FOV = 3.4
    GAIN = 1.62            # accumulated density -> brightness
    BLOOM = 178            # 0-255 multiplier on the blurred pass
    ABERRATION = 0.004     # chromatic split, as a fraction of the ring surface
                           # 0 turns it off; measured cost is in _energy()

    def __init__(self, state: HudState, fullscreen: bool = True):
        self.s = state
        self.t0 = time.time()
        self.t = 0.0
        self._sys = {"cpu": 0.0, "ram": 0.0, "disk": 0.0}
        self._sys_at = 0.0
        self._wave = [0.0] * 96
        self._hist_cpu = [0.0] * 90
        self._hist_ram = [0.0] * 90
        self._hist_seeded = False
        self._mic_open = False
        self._mic_rects: list = []
        self._confirm_rects: list = []
        self._chat_rects: list = []

        _prepare_display()
        pygame.init()
        pygame.display.set_caption("ULTRON")
        if fullscreen:
            # BORDERLESS, NOT EXCLUSIVE. pygame.FULLSCREEN asks Windows to
            # change the monitor's video mode, which resizes and rearranges
            # every other window on the desktop - and leaves them that way if
            # the process dies. A frameless window the exact size of the
            # desktop looks identical and touches nothing else.
            w, h = self._desktop_size()
            self.screen = pygame.display.set_mode((w, h), pygame.NOFRAME)
        else:
            self.screen = pygame.display.set_mode((1440, 860))
        self.W, self.H = self.screen.get_size()
        if self.W < 320 or self.H < 240:        # headless or odd driver
            self.W, self.H = 1440, 860
            self.screen = pygame.display.set_mode((self.W, self.H))
        # THE CURSOR STAYS VISIBLE. It was hidden in fullscreen for the usual
        # kiosk reason, but this screen is not a kiosk: the microphone dropdown
        # and the approval buttons are clicked with a mouse, and hiding the
        # pointer over them makes the interface look frozen.
        pygame.mouse.set_visible(True)
        if fullscreen:
            _raise_to_front(self.W, self.H)

        self._build_particles()
        self.layout()
        self.clock = pygame.time.Clock()

    @staticmethod
    def _desktop_size() -> tuple[int, int]:
        """Real pixels, asked for three different ways.

        get_desktop_sizes is the one that reports the desktop rather than the
        current video mode, which matters because a previous run that crashed in
        exclusive fullscreen can leave the mode changed."""
        try:
            w, h = pygame.display.get_desktop_sizes()[0]
            if w >= 320 and h >= 240:
                return int(w), int(h)
        except Exception:
            pass
        try:
            import ctypes
            u32 = ctypes.windll.user32
            w, h = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
            if w >= 320 and h >= 240:
                return int(w), int(h)
        except Exception:
            pass
        info = pygame.display.Info()
        return max(320, info.current_w), max(240, info.current_h)

    def layout(self) -> None:
        """Geometry and every buffer that depends on the screen size.

        Separate from __init__ so a resolution can be measured against without
        constructing a second window."""
        self.cx, self.cy = self.W // 2, int(self.H * 0.47)
        self.R = min(self.W, self.H) * 0.240
        self.u = min(self.W, self.H) / 860.0

        # THE ENERGY BOX. The ring and the wordmark only ever occupy a square
        # around the centre, so the accumulator covers that square and not the
        # whole screen. At 1080p that is 4x fewer pixels through numpy AND a 4x
        # cheaper upscale - the difference between 22fps and comfortably 60.
        # It is rendered at half resolution; the smooth upscale to full size is
        # itself part of what makes the particles soft.
        self.EB = max(16, int(self.R * 1.45 / 2) + 2)       # half-size, half-res
        side = self.EB * 2
        self._src = pygame.Surface((side, side)).convert()
        self._rgb = np.zeros((side, side, 3), np.uint8)
        self.BW = max(4, side // 10)                        # blur intermediate
        self._dest = pygame.Rect(self.cx - side, self.cy - side, side * 2, side * 2)

        self.ui = pygame.Surface((self.W, self.H), pygame.SRCALPHA).convert_alpha()

        self._build_fonts()
        self._build_word()
        self._build_bearings()
        self._bg = self._build_bg()

    def _build_bearings(self) -> None:
        """Compass marks around the ring, rendered once rather than every frame."""
        self._bearings = []
        rr = self.R * 1.50
        for deg in range(0, 360, 45):
            if deg == 180:          # the state label lives here
                continue
            a = math.radians(deg - 90)
            surf = self.f["tiny"].render(f"{deg:03d}", True, TEXT_FAINT)
            surf.set_alpha(150)
            pos = surf.get_rect(center=(int(self.cx + math.cos(a) * rr),
                                        int(self.cy + math.sin(a) * rr)))
            self._bearings.append((surf, pos))

    # ── built once ───────────────────────────────────────────────────────────

    def _build_particles(self, n: int = 13000) -> None:
        """Points on a torus, plus a per-particle strand phase.

        The reference ring is not one clean band - it is several wavy ribbons
        overlapping. Giving each particle one of six phase offsets makes the
        ripple pattern differ between strands, so they weave through each
        other instead of moving as one rigid hoop."""
        rng = np.random.default_rng(11)
        self._th = rng.uniform(0, 2 * math.pi, n).astype(np.float32)
        self._ph = rng.uniform(0, 2 * math.pi, n).astype(np.float32)
        # sqrt keeps points evenly spread across the tube's cross-section
        self._tr = np.sqrt(rng.uniform(0, 1, n)).astype(np.float32)
        self._w = (rng.uniform(0.34, 1.0, n) ** 1.15).astype(np.float32)
        self._ps = (rng.integers(0, 6, n) * 1.047).astype(np.float32)
        self._tube = 0.115

    def _build_fonts(self) -> None:
        def f(px, bold=False, mono=False):
            return pygame.font.SysFont(
                "consolas" if mono else "bahnschrift,segoeui,arial",
                max(8, int(px)), bold=bold)

        u = self.u                               # scales with the screen
        self.f = {
            "h": f(19 * u), "lbl": f(13 * u), "sm": f(12 * u), "xs": f(11 * u),
            "tiny": f(10 * u), "big": f(26 * u), "mono": f(13 * u, mono=True),
            "monosm": f(11 * u, mono=True),
        }

    def _build_word(self, text: str = "ULTRON") -> None:
        """The wordmark, letter-spaced by hand and cached as a density mask.

        It is fed into the same accumulator as the particles rather than blitted
        as text, so it blooms with the ring and picks up the same grain. That is
        why it reads as part of the hologram instead of a caption sitting on top
        of one."""
        target = self.R * 1.62
        probe = pygame.font.SysFont("bahnschrift,segoeui,arial", 100, bold=True)
        w100 = sum(probe.size(c)[0] for c in text) + 16 * (len(text) - 1)
        px = max(24, int(100 * target / max(1, w100)))

        font = pygame.font.SysFont("bahnschrift,segoeui,arial", px, bold=True)
        track = int(px * 0.16)
        glyphs = [font.render(c, True, (255, 255, 255)) for c in text]
        w = sum(g.get_width() for g in glyphs) + track * (len(glyphs) - 1)
        h = max(g.get_height() for g in glyphs)
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        x = 0
        for g in glyphs:
            surf.blit(g, (x, 0))
            x += g.get_width() + track

        # (x, y) order throughout, matching pygame.surfarray
        mask = pygame.surfarray.array_alpha(surf).astype(np.float32) / 255.0
        mask = mask[::2, ::2]                       # down to half resolution
        self._word_mask = mask

        rng = np.random.default_rng(5)
        grain = rng.random(mask.shape).astype(np.float32)
        grain = 0.30 + 0.70 * np.sqrt(grain)        # dust, with holes in it
        grain[:, ::3] *= 0.45                       # scan lines
        self._word_grain = grain

        mw, mh = mask.shape
        self._word_pos = (self.EB - mw // 2, self.EB - mh // 2)

    def _build_bg(self) -> pygame.Surface:
        """The backdrop, baked once.

        The near-black ground, a soft teal wash behind the ring so the black is
        not dead flat, and static far-field motes. None of it moves, so
        composing it every frame was three full-screen operations for a picture
        that never changes."""
        s = pygame.Surface((self.W, self.H))
        s.fill(BG)

        n = 48
        halo = pygame.Surface((n, n))
        for y in range(n):
            for x in range(n):
                d = math.hypot(x - (n - 1) / 2, y - (n - 1) / 2) / ((n - 1) / 2)
                v = max(0.0, 1.0 - d) ** 2.2
                halo.set_at((x, y), (int(8 * v), int(50 * v), int(46 * v)))
        side = max(8, int(self.R * 4.4))
        halo = pygame.transform.smoothscale(halo, (side, side))
        s.blit(halo, halo.get_rect(center=(self.cx, self.cy)),
               special_flags=pygame.BLEND_RGB_ADD)

        rng = np.random.default_rng(3)
        for _ in range(430):
            x, y = int(rng.integers(0, self.W)), int(rng.integers(0, self.H))
            v = float(rng.uniform(0.15, 0.75))
            s.set_at((x, y), (int(10 * v), int(46 * v), int(43 * v)))
        return s.convert()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def quit(self):
        self.s.running = False
        try:
            pygame.quit()
        except Exception:
            pass

    def pump(self) -> bool:
        if not self.s.running:
            return False
        try:
            for e in pygame.event.get():
                if e.type == pygame.QUIT:
                    self.quit()
                    return False
                if e.type == pygame.KEYDOWN:
                    # When the chat is open it captures typing, so ESC/RETURN/
                    # SPACE mean "close"/"send"/"a space", not quit/approve.
                    # A live approval still wins over the chat, deliberately.
                    if self.s.chat_open and not self.s.confirm:
                        self._chat_key(e)
                    elif e.key == pygame.K_ESCAPE:
                        if self._mic_open:
                            self._mic_open = False
                        else:
                            self.quit()
                            return False
                    elif e.key == pygame.K_RETURN and self.s.confirm:
                        self.s.confirm_result, self.s.confirm = True, None
                    elif e.key == pygame.K_SPACE and self.s.confirm:
                        self.s.confirm_result, self.s.confirm = False, None
                if e.type == pygame.MOUSEBUTTONDOWN:
                    self._click(e.pos)

            self.t += 1 / 60
            self._frame()
            pygame.display.flip()
            self.clock.tick(60)
            return True
        except pygame.error:
            self.s.running = False
            return False

    def _click(self, pos):
        if self.s.confirm:
            for rect, val in self._confirm_rects:
                if rect.collidepoint(pos):
                    self.s.confirm_result, self.s.confirm = val, None
                    return
            return
        # The open/close chat button, checked before the mic so it wins when
        # the mic dropdown happens to overlap it.
        for rect in self._chat_rects:
            if rect.collidepoint(pos):
                self.s.chat_open = not self.s.chat_open
                return
        for rect, idx in self._mic_rects:
            if rect.collidepoint(pos):
                if idx is None:
                    self._mic_open = not self._mic_open
                else:
                    self.s.device_request = idx
                    self._mic_open = False
                return
        self._mic_open = False

    # ── frame ────────────────────────────────────────────────────────────────

    def _frame(self):
        self.screen.blit(self._bg, (0, 0))
        self.ui.fill((0, 0, 0, 0))

        self._guides()
        self._energy()
        self._chrome()
        self._left_rail()
        self._watch_panel()
        self._audit_panel()
        self._clock_block()
        self._below_core()
        self._bottom_row()          # refreshes the readings _load_graph plots
        self._load_graph()
        if self.s.on_call:
            self._call_edges()
        self.screen.blit(self.ui, (0, 0))

        self._mic_picker()          # above everything else: it is interactive
        self._chat_panel()          # the desk chat: its button, and panel if open
        if self.s.confirm:
            self._confirm_panel()

    # ── the desk chat ──────────────────────────────────────────────────────────

    def _chat_key(self, e) -> None:
        """Keystrokes while the chat is open. Enter sends, Esc closes."""
        if e.key == pygame.K_ESCAPE:
            self.s.chat_open = False
        elif e.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self._chat_send()
        elif e.key == pygame.K_BACKSPACE:
            self.s.chat_input = self.s.chat_input[:-1]
        elif e.unicode and e.unicode.isprintable() and len(self.s.chat_input) < 500:
            self.s.chat_input += e.unicode

    def _chat_send(self) -> None:
        text = self.s.chat_input.strip()
        self.s.chat_input = ""
        if not text:
            return
        # Show it immediately as "you", and hand it to the listen thread to put
        # on the socket. The reply comes back as a normal "reply" event.
        self.s.chat_log.append(("you", text))
        del self.s.chat_log[:-200]
        try:
            self.s.outbox.put_nowait({"type": "say", "text": text})
        except Exception:
            pass

    def _wrap_chat(self, maxw: int) -> list:
        """Word-wrap the recent log to the panel width, as (who, line) rows."""
        fo = self.f["monosm"]
        out: list = []
        for who, text in self.s.chat_log[-40:]:
            prefix = "you> " if who == "you" else "jarvis> "
            cur = ""
            for w in (prefix + str(text)).split(" "):
                trial = (cur + " " + w).strip()
                if fo.size(trial)[0] <= maxw or not cur:
                    cur = trial
                else:
                    out.append((who, cur))
                    cur = w
            if cur:
                out.append((who, cur))
        return out

    def _fit_tail(self, s: str, font: str, maxw: int) -> str:
        """Keep the END of a string visible - the caret follows the typing."""
        fo = self.f[font]
        while s and fo.size(s)[0] > maxw:
            s = s[1:]
        return s

    def _chat_panel(self) -> None:
        """Left side: an OPEN/CLOSE CHAT button, and the chat panel when open."""
        u = self.u
        self._chat_rects = []
        bx, by = int(self.W * 0.038), int(self.H * 0.108)
        bw, bh = int(132 * u), int(26 * u)
        btn = pygame.Rect(bx, by, bw, bh)
        open_ = self.s.chat_open
        pygame.draw.rect(self.screen, (6, 20, 22), btn)
        pygame.draw.rect(self.screen, TEAL if open_ else TEAL_DEEP, btn, 1)
        self._txt("CLOSE CHAT" if open_ else "OPEN CHAT", "monosm",
                  TEAL_HOT if open_ else TEAL, btn.center, "center")
        self._chat_rects.append(btn)
        if not open_:
            return

        px, pw = int(self.W * 0.030), int(self.W * 0.300)
        ptop, pbot = int(self.H * 0.160), int(self.H * 0.905)
        panel = pygame.Rect(px, ptop, pw, pbot - ptop)
        veil = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
        veil.fill((3, 12, 15, 236))
        self.screen.blit(veil, (panel.left, panel.top))
        pygame.draw.rect(self.screen, TEAL_MID, panel, 1)
        pad = int(12 * u)
        self._txt("CHAT  -  DESK", "sm", TEAL, (panel.left + pad, panel.top + pad))

        # Input box pinned to the bottom, with a blinking caret.
        ih = int(30 * u)
        ibox = pygame.Rect(panel.left + pad, panel.bottom - pad - ih,
                           panel.w - 2 * pad, ih)
        pygame.draw.rect(self.screen, (5, 16, 18), ibox)
        pygame.draw.rect(self.screen, TEAL, ibox, 1)
        caret = "_" if int(self.t * 2) % 2 == 0 else " "
        shown = self._fit_tail("> " + self.s.chat_input + caret, "mono",
                               ibox.w - int(12 * u))
        self._txt(shown, "mono", TEAL_HOT,
                  (ibox.left + int(6 * u), ibox.centery), "midleft")

        # The log, between title and input, newest at the bottom.
        log_top = panel.top + pad + int(24 * u)
        log_bot = ibox.top - int(8 * u)
        lh = self.f["monosm"].get_height() + int(3 * u)
        maxlines = max(1, (log_bot - log_top) // lh)
        rows = self._wrap_chat(panel.w - 2 * pad)
        if not rows:
            self._txt("type below and press Enter", "monosm", TEXT_FAINT,
                      (panel.left + pad, log_top))
        for who, line in rows[-maxlines:]:
            self._txt(line, "monosm", TEAL if who == "you" else TEXT,
                      (panel.left + pad, log_top))
            log_top += lh

    # ── the ring ─────────────────────────────────────────────────────────────

    def _energy(self):
        """Particles and wordmark into one float accumulator, then bloom."""
        side = self.EB * 2
        cx = cy = float(self.EB)            # box centre, in half-res pixels
        R = self.R / 2.0
        col = STATE_COLOR.get(self.s.state, TEAL_MID)
        spin = {"listening": 0.6, "thinking": 1.45, "acting": 1.1,
                "speaking": 0.85}.get(self.s.state, 0.3)
        lvl = max(0.0, min(1.0, self.s.level))
        t = self.t * spin

        th, ph, tr, ps = self._th, self._ph, self._tr, self._ps

        # four layered waves: the band ripples instead of being a clean annulus
        rip = (0.052 * np.sin(3 * th + t * 0.55 + ps)
               + 0.036 * np.sin(5 * th - t * 0.42 + ps * 1.7)
               + 0.024 * np.sin(8 * th + t * 0.83 + ps * 2.3)
               + 0.015 * np.sin(13 * th - t * 1.10))
        major = 1.0 + rip + lvl * 0.03
        tube = (self._tube * (0.72 + 0.46 * np.sin(2 * th + t * 0.7 + ps))
                * (1.0 + lvl * 0.28))

        rr = major + tube * tr * np.cos(ph)
        x = rr * np.cos(th)
        y = rr * np.sin(th)
        z = tube * tr * np.sin(ph)

        # slow wobble, so it reads as a ring in space and not a drawn circle
        ax, ay = 0.15 * math.sin(self.t * 0.13), 0.19 * math.sin(self.t * 0.10)
        ca, sa = math.cos(ax), math.sin(ax)
        cb, sb = math.cos(ay), math.sin(ay)
        y, z = y * ca - z * sa, y * sa + z * ca
        x, z = x * cb + z * sb, -x * sb + z * cb

        d = self.FOV / (self.FOV - z)
        px = cx + x * R * d
        py = cy + y * R * d

        nz = np.clip(z / (self._tube * 1.25) * 0.5 + 0.5, 0.0, 1.0)
        bright = self._w * (0.58 + 0.52 * nz)

        ok = (px >= 0) & (px < side) & (py >= 0) & (py < side)
        idx = px[ok].astype(np.int32) * side + py[ok].astype(np.int32)
        acc = np.bincount(idx, weights=bright[ok],
                          minlength=side * side).astype(np.float32)
        acc = acc.reshape(side, side)

        # the wordmark, as density rather than as text
        m = self._word_mask
        gx, gy = self._word_pos
        mw, mh = m.shape
        if 0 <= gx and gx + mw <= side and 0 <= gy and gy + mh <= side:
            roll = int(self.t * 13) % max(1, mh)
            grain = np.roll(self._word_grain, roll, axis=1)
            acc[gx:gx + mw, gy:gy + mh] += m * grain * (1.24 + 0.26 * lvl)

        acc *= self.GAIN
        np.clip(acc, 0.0, 2.0, out=acc)
        base = np.minimum(acc, 1.0)
        hot = acc - 1.0
        np.clip(hot, 0.0, 1.0, out=hot)
        hot *= hot                                  # only real pile-ups whiten
        self._rgb[..., 0] = base * col[0] + hot * (255 - col[0])
        self._rgb[..., 1] = base * col[1] + hot * (255 - col[1])
        self._rgb[..., 2] = base * col[2] + hot * (255 - col[2])
        pygame.surfarray.blit_array(self._src, self._rgb)

        # The blur IS the bloom: average down, spread back up, add once. The
        # dimming happens on the TINY surface - a full-screen BLEND_RGB_MULT
        # measured 9.9ms a frame on its own, which is a sixth of a 60fps budget
        # spent turning one number down.
        tiny = pygame.transform.smoothscale(self._src, (self.BW, self.BW))
        tiny.fill((self.BLOOM,) * 3, special_flags=pygame.BLEND_RGB_MULT)
        blur = pygame.transform.smoothscale(tiny, (side, side))
        self._src.blit(blur, (0, 0), special_flags=pygame.BLEND_RGB_ADD)

        # Chromatic aberration, borrowed from the ULTRON orb UI's post stack
        # (its shader offsets R outward and B inward from centre, then adds a
        # faint flicker). There is no fragment shader here, so the same thing
        # is done with scale: red sampled from a slightly larger copy, blue
        # from a slightly smaller one, green left where it is. Done on the
        # SMALL surface, before the upscale - at full size it would cost more
        # than the effect is worth.
        if self.ABERRATION:
            d = max(1, int(side * self.ABERRATION))
            red = pygame.transform.smoothscale(self._src,
                                               (side + 2 * d, side + 2 * d))
            blue = pygame.transform.smoothscale(self._src,
                                                (side - 2 * d, side - 2 * d))
            red.fill((255, 0, 0), special_flags=pygame.BLEND_RGB_MULT)
            blue.fill((0, 0, 255), special_flags=pygame.BLEND_RGB_MULT)
            self._src.fill((0, 255, 0), special_flags=pygame.BLEND_RGB_MULT)
            self._src.blit(red, (-d, -d), special_flags=pygame.BLEND_RGB_ADD)
            self._src.blit(blue, (d, d), special_flags=pygame.BLEND_RGB_ADD)

        big = pygame.transform.smoothscale(self._src, (side * 2, side * 2))
        # The orb's shader flickers by ~2% on two out-of-phase sines, which
        # reads as a live projection rather than a still image.
        flick = 1.0 + 0.02 * math.sin(self.t * 30.0) * math.sin(self.t * 7.3)
        if abs(flick - 1.0) > 0.002:
            v = max(0, min(255, int(255 * flick)))
            big.fill((v, v, v), special_flags=pygame.BLEND_RGB_MULT)
        self.screen.blit(big, self._dest, special_flags=pygame.BLEND_RGB_ADD)

    def _guides(self):
        """Faint structure under the ring: circles, a ghost globe, ticks."""
        cx, cy, R = self.cx, self.cy, self.R
        col = STATE_COLOR.get(self.s.state, TEAL_MID)

        for f, a in ((0.30, 26), (0.44, 34), (0.60, 24), (0.88, 44)):
            pygame.draw.circle(self.ui, (*TEAL, a), (cx, cy), int(R * f), 1)

        # ghost globe: latitudes as flattened ellipses, longitudes as narrow ones
        gr = R * 0.46
        for i in range(1, 6):
            yy = cy + gr * math.sin(math.pi * (i / 6.0 - 0.5))
            rx = gr * math.cos(math.asin(max(-1.0, min(1.0, (yy - cy) / gr))))
            ry = max(1.0, rx * 0.16)
            if rx > 2:
                pygame.draw.ellipse(self.ui, (*TEAL, 22),
                                    (cx - rx, yy - ry, rx * 2, ry * 2), 1)
        for i in range(6):
            rx = abs(gr * math.cos(self.t * 0.08 + i * math.pi / 6))
            if rx > 2:
                pygame.draw.ellipse(self.ui, (*TEAL, 20),
                                    (cx - rx, cy - gr, rx * 2, gr * 2), 1)

        # tick ring
        for i in range(120):
            ang = math.radians(i * 3) + self.t * 0.07
            long = i % 10 == 0
            r1 = R * 1.24
            r2 = R * (1.30 if long else 1.265)
            p1 = (cx + math.cos(ang) * r1, cy + math.sin(ang) * r1)
            p2 = (cx + math.cos(ang) * r2, cy + math.sin(ang) * r2)
            pygame.draw.line(self.ui, (*TEAL, 96 if long else 40), p1, p2, 1)

        sweep = math.radians((self.t * 34) % 360)
        rect = pygame.Rect(cx - R * 1.34, cy - R * 1.34, R * 2.68, R * 2.68)
        pygame.draw.arc(self.ui, (*col, 120), rect, sweep, sweep + 0.85, 2)

        # Heavy outer brackets, turning the other way. Two rings moving in
        # opposite directions is what stops a HUD reading as a static picture.
        back = -self.t * 0.19
        rr = R * 1.42
        obr = pygame.Rect(cx - rr, cy - rr, rr * 2, rr * 2)
        for i in range(3):
            a0 = back + i * 2.094
            pygame.draw.arc(self.ui, (*TEAL, 150), obr, a0, a0 + 0.62,
                            max(2, int(3 * self.u)))
            tipx = cx + math.cos(a0) * rr
            tipy = cy - math.sin(a0) * rr       # pygame arcs run anticlockwise
            pygame.draw.circle(self.ui, (*TEAL_HOT, 190), (int(tipx), int(tipy)),
                               max(2, int(3 * self.u)))

        # dashed inner circle, turning slowly
        dr = R * 1.06
        for i in range(0, 72, 2):
            a0 = math.radians(i * 5) + self.t * 0.11
            a1 = a0 + math.radians(3.2)
            pygame.draw.line(self.ui, (*TEAL, 58),
                             (cx + math.cos(a0) * dr, cy + math.sin(a0) * dr),
                             (cx + math.cos(a1) * dr, cy + math.sin(a1) * dr), 1)

        # bearing labels, pre-rendered once
        for surf, pos in self._bearings:
            self.ui.blit(surf, pos)

    # ── text helpers ─────────────────────────────────────────────────────────

    def _txt(self, s, font, color, pos, anchor="topleft", alpha=None):
        surf = self.f[font].render(str(s), True, color)
        if alpha is not None:
            surf.set_alpha(alpha)
        r = surf.get_rect(**{anchor: pos})
        self.screen.blit(surf, r)
        return r

    def _uitxt(self, s, font, color, pos, anchor="topleft", alpha=255):
        surf = self.f[font].render(str(s), True, color)
        surf.set_alpha(alpha)
        r = surf.get_rect(**{anchor: pos})
        self.ui.blit(surf, r)
        return r

    # ── chrome ───────────────────────────────────────────────────────────────

    def _chrome(self):
        """Top caption on a hairline, and corner marks. Nothing loud."""
        y = int(self.H * 0.045)
        cap = self.f["sm"].render("U L T R O N", True, TEXT)
        cap.set_alpha(190)
        r = cap.get_rect(midtop=(self.W // 2, y - cap.get_height() // 2))
        self.ui.blit(cap, r)
        pygame.draw.line(self.ui, (*TEAL, 70), (int(self.W * 0.30), y),
                         (r.left - 18, y), 1)
        pygame.draw.line(self.ui, (*TEAL, 70), (r.right + 18, y),
                         (int(self.W * 0.70), y), 1)

        # A display client that has lost its link looks identical to one where
        # nothing is happening. Say which it is, or the screen starts lying.
        if not self.s.connected:
            self._uitxt("OFFLINE - no link to jarvis", "xs", AMBER,
                        (self.W // 2, y + int(14 * self.u)), "midtop")

        m, L = int(self.W * 0.013), int(self.W * 0.026)
        for ax, ay, dx, dy in ((m, m, 1, 1), (self.W - m, m, -1, 1),
                               (m, self.H - m, 1, -1),
                               (self.W - m, self.H - m, -1, -1)):
            pygame.draw.line(self.ui, (*TEAL, 80), (ax, ay), (ax + L * dx, ay), 2)
            pygame.draw.line(self.ui, (*TEAL, 80), (ax, ay), (ax, ay + L * dy), 2)

    def _clock_block(self):
        """Date and time, top right."""
        x, y = self.W - int(self.W * 0.035), int(self.H * 0.050)
        u = self.u
        self._uitxt(time.strftime("%H:%M:%S"), "big", WHITE, (x, y), "topright",
                    alpha=225)
        self._uitxt(time.strftime("%A, %d %B %Y").upper(), "xs", TEXT_DIM,
                    (x, y + int(34 * u)), "topright")

        live = self.s.state != "error"
        col = GREEN if live else RED
        p = 0.55 + 0.45 * math.sin(self.t * 2.2)
        dot = (x - int(78 * u), y + int(58 * u))
        pygame.draw.circle(self.ui, (*col, int(120 + 110 * p)), dot,
                           max(2, int(4 * u)))
        self._uitxt("ONLINE" if live else "FAULT", "xs", col,
                    (dot[0] + int(10 * u), dot[1] - int(7 * u)), alpha=210)

        cxr = x
        for name, on in reversed((("DESK", True), ("TELEGRAM", self.s.telegram),
                                  ("PHONE", self.s.phone))):
            r = self._uitxt(name, "tiny", TEAL if on else TEXT_FAINT,
                            (cxr, y + int(78 * u)), "topright",
                            alpha=200 if on else 120)
            cxr = r.left - int(14 * u)

    def _left_rail(self):
        """The four stages, as a thin rail rather than a panel."""
        st = self.s.state
        x = int(self.W * 0.038)
        y = int(self.H * 0.34)
        step = int(self.H * 0.062)
        pygame.draw.line(self.ui, (*TEAL, 52), (x, y - int(10 * self.u)),
                         (x, y + step * 3 + int(10 * self.u)), 1)
        for name, active in STAGES:
            on = st in active
            col = TEAL_HOT if on else TEXT_FAINT
            rad = max(2, int(4 * self.u))
            pygame.draw.circle(self.ui, (*col, 255 if on else 190), (x, y), rad)
            if on:
                pygame.draw.circle(self.ui, (*col, 60), (x, y), rad * 3, 1)
            self._uitxt(name, "sm", TEXT if on else TEXT_FAINT,
                        (x + int(16 * self.u), y - int(8 * self.u)),
                        alpha=245 if on else 195)
            y += step

    def _load_graph(self):
        """Ninety seconds of real CPU and memory, under the stage rail.

        The left half of the screen was empty, and an empty half reads as a
        thing that is not doing anything. This is measured, not decorative -
        every value on this screen is real, and these two are the ones that tell
        you whether the machine is actually busy."""
        u = self.u
        x = int(self.W * 0.038)
        w = int(self.W * 0.155)
        y = int(self.H * 0.615)
        h = int(self.H * 0.085)

        self._uitxt("SYSTEM LOAD", "sm", TEAL, (x, y), alpha=225)
        y += int(20 * u)
        pygame.draw.line(self.ui, (*TEAL, 66), (x, y), (x + w, y), 1)
        y += int(8 * u)

        for i in range(1, 4):                       # grid
            gy = y + int(h * i / 4)
            pygame.draw.line(self.ui, (*TEAL, 22), (x, gy), (x + w, gy), 1)

        for series, colour, alpha in ((self._hist_cpu, TEAL, 235),
                                      (self._hist_ram, TEAL_MID, 165)):
            n = len(series)
            pts = [(x + w * i / (n - 1), y + h * (1.0 - min(1.0, v)))
                   for i, v in enumerate(series)]
            pygame.draw.lines(self.ui, (*colour, alpha), False, pts,
                              max(1, int(1.6 * u)))

        self._uitxt("CPU", "tiny", TEAL, (x, y + h + int(4 * u)), alpha=210)
        self._uitxt("MEM", "tiny", TEAL_MID,
                    (x + int(34 * u), y + h + int(4 * u)), alpha=175)
        self._uitxt(f"{self._sys['cpu']:.0f}%  /  {self._sys['ram']:.0f}%",
                    "tiny", TEXT_DIM, (x + w, y + h + int(4 * u)), "topright")

    def _audit_panel(self):
        """Every run_python call, with its FULL source, as it runs.

        Code only runs at the desk - and this is why that is worth something.
        The boundary in safety.py stops phone and Telegram from running code
        at all; this panel is the other half of the same idea, because the one
        place that CAN run code shows you exactly what it ran. Nothing is
        summarised, nothing is elided to a name: the source itself is on the
        screen, while it executes.

        Newest first, oldest scrolled off the bottom."""
        u = self.u
        x = int(self.W * 0.038)
        w = int(self.W * 0.30)
        y = int(self.H * 0.735)
        floor = int(self.H * 0.905)      # _bottom_row owns this column below

        self._uitxt("AUDIT - CODE RUN AT THIS DESK", "sm", TEAL, (x, y),
                    alpha=225)
        y += int(20 * u)
        pygame.draw.line(self.ui, (*TEAL, 66), (x, y), (x + w, y), 1)
        y += int(11 * u)

        if not self.s.audit:
            self._uitxt("no code has run", "xs", TEXT_DIM, (x, y))
            return

        line_h = int(15 * u)
        for entry in self.s.audit:
            if y > floor - line_h * 2:
                break
            ok = entry.get("ok")
            dot = TEXT_DIM if ok is None else (GREEN if ok else RED)
            pygame.draw.circle(self.ui, (*dot, 230),
                               (x + int(4 * u), y + int(6 * u)),
                               max(2, int(3 * u)))
            self._uitxt(str(entry.get("why", ""))[:52], "xs", TEXT,
                        (x + int(16 * u), y), alpha=235)
            y += int(18 * u)

            for ln in str(entry.get("code", "")).splitlines():
                if y > floor - line_h:
                    self._uitxt("...", "monosm", TEXT_FAINT,
                                (x + int(16 * u), y))
                    y += line_h
                    break
                self._uitxt(ln[:58] or " ", "monosm", TEAL_MID,
                            (x + int(16 * u), y), alpha=205)
                y += line_h

            out = str(entry.get("result", "") or "").replace("\n", " ")
            if out:
                self._uitxt(out[:56], "tiny", dot, (x + int(16 * u), y),
                            alpha=215)
                y += int(16 * u)
            y += int(8 * u)
            pygame.draw.line(self.ui, (*TEAL, 30), (x, y - int(5 * u)),
                             (x + w, y - int(5 * u)), 1)

    def _watch_panel(self):
        """Live watch readings, right side. Thin rules, no boxes."""
        u = self.u
        x = int(self.W * 0.735)
        w = int(self.W - x - self.W * 0.038)
        y = int(self.H * 0.33)
        self._uitxt("LIVE WATCH", "sm", TEAL, (x, y), alpha=225)
        y += int(20 * u)
        pygame.draw.line(self.ui, (*TEAL, 66), (x, y), (x + w, y), 1)
        y += int(12 * u)

        if not self.s.watches:
            self._uitxt("nothing being watched", "xs", TEXT_DIM, (x, y))
            self._uitxt('say "watch my sales every hour"', "tiny", TEXT_FAINT,
                        (x, y + int(18 * u)))
            return

        for wj in self.s.watches[:4]:
            ok = wj.get("ok", True)
            c = GREEN if ok else AMBER
            pygame.draw.circle(self.ui, (*c, 220),
                               (x + int(4 * u), y + int(7 * u)),
                               max(2, int(3 * u)))
            self._uitxt(str(wj["name"])[:22].upper(), "xs", TEXT,
                        (x + int(16 * u), y), alpha=225)
            self._uitxt(str(wj.get("every", "")), "tiny", TEXT_FAINT,
                        (x + w, y + int(2 * u)), "topright")
            self._uitxt(str(wj.get("last", ""))[:46], "tiny", TEAL_MID,
                        (x + int(16 * u), y + int(17 * u)), alpha=210)
            y += int(44 * u)
            pygame.draw.line(self.ui, (*TEAL, 34), (x, y - int(9 * u)),
                             (x + w, y - int(9 * u)), 1)

    def _below_core(self):
        """State, the live waveform, and whatever is being worked on."""
        u = self.u
        cx = self.W // 2
        y = int(self.cy + self.R * 1.52)
        col = STATE_COLOR.get(self.s.state, TEAL_MID)
        self._uitxt(STATE_LABEL.get(self.s.state, ""), "h", col, (cx, y), "midtop")

        active = self.s.state in ("listening", "speaking", "call_speaking")
        self._wave.append(self.s.level if active else 0.0)
        self._wave.pop(0)
        # Wider spacing and alpha that follows the level. Packed full-alpha bars
        # of equal height do not read as a waveform - they read as a solid bar
        # sitting under the ring.
        bw = max(3, int(5 * u))     # any wider and it reaches the stats row
        mid = y + int(62 * u)       # clear of the label: tall bars reached into it
        span = len(self._wave) * bw
        for i, v in enumerate(self._wave):
            h = max(1, int(v * 30 * u + 1 * u))
            xx = cx - span // 2 + i * bw
            a = 60 + int(195 * min(1.0, v * 1.6)) if active else 52
            pygame.draw.line(self.ui, (*col, a), (xx, mid - h), (xx, mid + h),
                             max(1, bw - 3))

        hint = self.s.task or STATE_HINT.get(self.s.state, "")
        if self.s.tool:
            hint = self.s.tool.replace("_", " ") + "  -  " + str(hint)
        self._uitxt(str(hint)[:72], "sm", TEXT_DIM,
                    (cx, mid + int(34 * u)), "midtop")

    def _bottom_row(self):
        now = time.time()
        if now - self._sys_at > 1.2:
            self._sys_at = now
            try:
                self._sys["cpu"] = psutil.cpu_percent()
                self._sys["ram"] = psutil.virtual_memory().percent
                self._sys["disk"] = psutil.disk_usage("C:/").percent
            except Exception:
                pass
            if not self._hist_seeded:
                # Otherwise the first 90 seconds show a flat zero line with a
                # cliff at the right, which looks like a broken graph.
                self._hist_seeded = True
                self._hist_cpu = [self._sys["cpu"] / 100.0] * len(self._hist_cpu)
                self._hist_ram = [self._sys["ram"] / 100.0] * len(self._hist_ram)
            self._hist_cpu.append(self._sys["cpu"] / 100.0)
            self._hist_cpu.pop(0)
            self._hist_ram.append(self._sys["ram"] / 100.0)
            self._hist_ram.pop(0)
        up = int(now - self.t0)
        items = [("CPU", f"{self._sys['cpu']:.0f}%", self._sys["cpu"] / 100),
                 ("MEM", f"{self._sys['ram']:.0f}%", self._sys["ram"] / 100),
                 ("DISK", f"{self._sys['disk']:.0f}%", self._sys["disk"] / 100),
                 ("SPEND", f"${self.s.spend:.4f}", None),
                 ("UPTIME", f"{up // 3600:02d}:{(up % 3600) // 60:02d}", None),
                 ("REPLY", f"{self.s.latency:.2f}s" if self.s.latency else "-", None)]

        u = self.u
        x = int(self.W * 0.038)
        y = int(self.H - self.H * 0.075)
        bar = int(52 * u)
        for label, val, frac in items:
            self._uitxt(label, "tiny", TEXT_FAINT, (x, y))
            col = AMBER if (frac is not None and frac > 0.85) else TEAL
            self._uitxt(val, "monosm", col, (x, y + int(14 * u)), alpha=225)
            if frac is not None:
                yy = y + int(32 * u)
                pygame.draw.line(self.ui, (*TEAL, 58), (x, yy), (x + bar, yy), 2)
                pygame.draw.line(self.ui, (*col, 230), (x, yy),
                                 (x + int(bar * min(frac, 1.0)), yy), 2)
            x += int(72 * u)

        if self.s.model:
            self._uitxt(self.s.model.replace("gpt-", ""), "tiny", TEXT_FAINT,
                        (self.W - int(self.W * 0.038), y + int(14 * u)),
                        "topright")

    def _mic_picker(self):
        """Top left, as asked: the input device, with a dropdown."""
        u = self.u
        self._mic_rects = []
        x, y = int(self.W * 0.038), int(self.H * 0.050)
        w, h = int(300 * u), int(30 * u)
        self._txt("INPUT DEVICE", "tiny", TEXT_FAINT, (x, y))
        head = pygame.Rect(x, y + int(16 * u), w, h)
        pygame.draw.rect(self.screen, (5, 16, 18), head)
        pygame.draw.rect(self.screen, TEAL_MID if self._mic_open else TEAL_DEEP,
                         head, 1)
        active = next((d for d in self.s.devices if d.get("active")), None)
        label = str((active or {}).get("name", "detecting..."))[:32]
        self._txt(label, "monosm", TEAL,
                  (head.left + int(9 * u), head.centery), "midleft")
        ax = head.right - int(14 * u)
        pygame.draw.polygon(self.screen, TEAL_MID,
                            [(ax - 5, head.centery - 2),
                             (ax + 5, head.centery - 2),
                             (ax, head.centery + 4)])
        self._mic_rects.append((head, None))

        if self._mic_open:
            oy = head.bottom
            for d in self.s.devices[:6]:
                r = pygame.Rect(x, oy, w, int(26 * u))
                pygame.draw.rect(self.screen, (6, 20, 22), r)
                pygame.draw.rect(self.screen, TEAL_DEEP, r, 1)
                self._txt(str(d["name"])[:34], "monosm",
                          TEAL_HOT if d.get("active") else TEXT,
                          (r.left + int(9 * u), r.centery), "midleft")
                self._mic_rects.append((r, d["index"]))
                oy = r.bottom

    def _call_edges(self):
        """A call is live: the frame goes red and the desk mic is locked."""
        p = 0.5 + 0.5 * abs(math.sin(self.t * 1.7))
        t = max(2, int(4 * self.u))
        a = int(120 + 110 * p)
        for r in (pygame.Rect(0, 0, self.W, t),
                  pygame.Rect(0, self.H - t, self.W, t),
                  pygame.Rect(0, 0, t, self.H),
                  pygame.Rect(self.W - t, 0, t, self.H)):
            pygame.draw.rect(self.ui, (*RED, a), r)
        self._uitxt("ON A CALL  -  DESK MICROPHONE LOCKED", "h", RED,
                    (self.W // 2, int(self.H * 0.085)), "midtop", alpha=235)
        if self.s.caller:
            self._uitxt(self.s.caller, "monosm", TEXT_DIM,
                        (self.W // 2, int(self.H * 0.085) + int(26 * self.u)),
                        "midtop")

    def _confirm_panel(self):
        info = self.s.confirm or {}
        u = self.u
        w, h = int(self.W * 0.62), int(self.H * 0.6)
        r = pygame.Rect((self.W - w) // 2, (self.H - h) // 2, w, h)
        veil = pygame.Surface((self.W, self.H), pygame.SRCALPHA)
        veil.fill((1, 4, 6, 222))
        self.screen.blit(veil, (0, 0))
        pygame.draw.rect(self.screen, (7, 18, 22), r)
        pygame.draw.rect(self.screen, AMBER, r, 2)
        pad = int(20 * u)
        self._txt(f"APPROVAL REQUIRED  -  {info.get('summary', 'flagged')}", "h",
                  AMBER, (r.left + pad, r.top + pad))
        y = r.top + pad + int(34 * u)
        for f in (info.get("findings") or [])[:3]:
            self._txt(f"line {f.get('lineno')}  [{f.get('severity')}]  "
                      f"{str(f.get('detail', ''))[:64]}", "sm", AMBER,
                      (r.left + pad, y))
            y += int(20 * u)
        y += int(10 * u)
        flagged = set(info.get("flagged_lines") or [])
        for i, line in enumerate((info.get("code") or "").split("\n")[:18], 1):
            col = (255, 217, 138) if i in flagged else (186, 224, 222)
            if i in flagged:
                pygame.draw.rect(self.screen, (52, 38, 14),
                                 (r.left + pad - 4, y - 2, w - 2 * pad + 8,
                                  int(18 * u)))
            self._txt(f"{i:3d} | {line[:78]}", "monosm", col, (r.left + pad, y))
            y += int(18 * u)

        self._confirm_rects = []
        bw, bh = int(118 * u), int(34 * u)
        for label, val, off in (("RUN IT", True, 1), ("DECLINE", False, 2)):
            b = pygame.Rect(r.right - pad - off * (bw + int(14 * u)),
                            r.bottom - pad - bh, bw, bh)
            pygame.draw.rect(self.screen, (9, 22, 26), b)
            pygame.draw.rect(self.screen, AMBER if val else TEXT_DIM, b, 1)
            self._txt(label, "sm", AMBER if val else TEXT, b.center, "center")
            self._confirm_rects.append((b, val))
        self._txt("ENTER to run   -   SPACE to decline", "tiny", TEXT_DIM,
                  (r.left + pad, r.bottom - pad - int(14 * u)))




# ─────────────────────────────────────────────────────────────────────────────
# THE DISPLAY CLIENT — this process renders, and does nothing else.
#
# It holds no model, no tools, no database and no decisions. It opens one
# WebSocket to main.py, turns the events it receives into HudState fields, and
# draws them. If main.py is not running, this shows "OFFLINE" and keeps trying;
# it never does the work itself, because then there would be two Jarvises.
#
# THIS IS WHY THE HUD SURVIVED THE REBUILD. The old desk.py had the wake word,
# the microphone, the session, the router and the tool loop living behind the
# same window - so the screen going away took Jarvis with it, and a second
# channel meant a second brain with its own idea of the conversation. Now the
# screen is a client like any other. Close it and nothing stops.
# ─────────────────────────────────────────────────────────────────────────────

import json
import threading

HUD_URL = os.environ.get(
    "JARVIS_HUD_URL",
    f"ws://127.0.0.1:{os.environ.get('JARVIS_PORT', '5051')}/hud")
AUDIT_KEEP = 12


def _apply(s: HudState, ev: dict) -> None:
    """One event -> fields on the state. Pure mapping, no logic.

    Both event dialects are accepted: brain.py names the tool `name`, and
    realtime.py names it `tool`. Rather than make one of them change and risk
    breaking the phone path, this reads either."""
    kind = ev.get("type")
    tool = ev.get("name") or ev.get("tool") or ""

    if kind == "hello":
        s.connected = True

    elif kind == "state":
        s.state = ev.get("state", s.state)

    elif kind == "heard":
        # Show WHERE it came from. Three channels reach the same Jarvis, and
        # a line on screen with no source is ambiguous - he could not tell
        # whether the message he had just sent from his phone had landed.
        via = str(ev.get("via", "") or "")
        text = str(ev.get("text", ""))[:80]
        s.task = f"[{via}] {text}" if via else text
        s.state = "thinking"

    elif kind == "transcript":
        s.task = str(ev.get("text", ""))[:90]

    elif kind == "route":
        s.model = str(ev.get("model", ""))

    elif kind == "plan":
        # The plan Jarvis made before acting: task, helpers, steps. Shown in
        # the chat so the Boss sees what it decided and with what.
        task = str(ev.get("task", "")).strip()
        helpers = [str(h) for h in (ev.get("helpers") or [])]
        steps = [str(x) for x in (ev.get("steps") or [])]
        line = "PLAN: " + task
        if helpers:
            line += "  -  using " + ", ".join(helpers)
        for i, st in enumerate(steps):
            line += f"\n  {i + 1}. {st}"
        s.chat_log.append(("jarvis", line))
        del s.chat_log[:-200]
        s.state = "thinking"
        s.task = ("plan: " + task)[:90]

    elif kind in ("tool", "tool_call"):
        s.state = "acting"
        s.tool = tool
        # The full source, unabridged. run_python is the only tool that can
        # touch the machine, so it is the only one worth this much screen.
        s.audit.insert(0, {"tool": tool,
                           "why": ev.get("why") or tool,
                           "code": ev.get("code", ""),
                           "ok": None, "result": ""})
        del s.audit[AUDIT_KEEP:]

    elif kind == "tool_result":
        s.tool = ""
        for entry in s.audit:                 # newest first, so the first
            if entry["ok"] is None and entry["tool"] == tool:   # open one wins
                entry["ok"] = bool(ev.get("ok"))
                entry["result"] = str(ev.get("detail", ""))[:160]
                break

    elif kind == "reply":
        s.state = "speaking"
        s.task = str(ev.get("text", ""))[:90]
        # Also show the full reply in the desk chat, so a typed conversation
        # reads as a conversation rather than a one-line status.
        reply = str(ev.get("text", "")).strip()
        if reply:
            s.chat_log.append(("jarvis", reply))
            del s.chat_log[:-200]

    elif kind == "cost":
        s.spend += float(ev.get("turn_usd") or 0.0)

    elif kind == "call":
        on = ev.get("state") == "connected"
        s.on_call = on
        s.state = "call" if on else "idle"

    elif kind == "error":
        s.state = "error"
        s.task = str(ev.get("error", ""))[:90]

    elif kind == "jobs":
        s.watches = list(ev.get("watches") or [])


def _listen(s: HudState) -> None:
    """Receive forever, in a thread. Reconnects; never gives up quietly."""
    from websockets.sync.client import connect

    while s.running:
        try:
            with connect(HUD_URL, open_timeout=4) as ws:
                s.connected = True
                while s.running:
                    try:
                        _apply(s, json.loads(ws.recv(timeout=1.0)))
                    except TimeoutError:
                        pass
                    # Send anything the chat has queued. Done on THIS thread,
                    # which owns the socket - the sync client is not safe to
                    # send on from the pump thread. Up to ~1s latency, which is
                    # invisible next to the model turn that follows.
                    try:
                        while True:
                            ws.send(json.dumps(s.outbox.get_nowait()))
                    except queue.Empty:
                        pass
        except Exception:
            s.connected = False
            s.model = ""
            time.sleep(2.0)          # main.py is down or restarting


if __name__ == "__main__":
    import sys

    st = HudState(telegram=bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                  phone=bool(os.environ.get("TWILIO_ACCOUNT_SID")))
    threading.Thread(target=_listen, args=(st,), daemon=True).start()

    hud = HudGL(st, fullscreen="--windowed" not in sys.argv)
    while hud.pump():
        pass
    st.running = False
