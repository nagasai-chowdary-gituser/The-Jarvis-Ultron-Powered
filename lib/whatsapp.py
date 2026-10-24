"""
lib/whatsapp.py — the Boss's own WhatsApp DESKTOP APP, driven by selector.

WHAT THIS IS, AND WHY IT IS NOT GUI AUTOMATION

    WhatsApp Desktop on Windows looks like a native app - WinUI shell, its own
    window, its own taskbar icon. Walking its accessibility tree shows why it
    can be driven properly anyway:

        PaneControl 'WhatsApp - Web content'      <- a WebView2

    The app is web.whatsapp.com running inside embedded Chromium. So it speaks
    the Chrome DevTools Protocol, and every action below is a CSS selector
    against the DOM - the same mechanism as browser.py, not pixels.

    That matters because the alternatives were both bad. The old build captured
    the screen, asked a vision model where things were, and clicked a
    COORDINATE: 5-15 seconds a step, and a list that scrolled meant a message
    to the wrong person - which happened. Driving the native accessibility
    tree would have worked but is a weaker contract than the DOM.

    THREE THINGS THIS BUYS: it is HIS account, already signed in, so there is
    nothing to pair and no second number. It is the real client, so there is no
    whatsmeow ban risk. And messages come from him because they ARE from him.

HOW "WRONG CHAT" IS MADE STRUCTURALLY IMPOSSIBLE

    WhatsApp labels its message box with the recipient's name:

        aria-label="Type a message to +91 98765 43210"

    So after opening a chat, send() reads that label back off the screen and
    refuses to type unless it names the contact that was asked for. The guard
    is not care, it is a check - and it reads the same element that will
    receive the keystrokes, so there is no gap between what was verified and
    what is typed into.

SELECTORS ARE ARIA AND data-*, NEVER CLASS NAMES
    WhatsApp ships obfuscated, rotating class names (x1c4vz4f, xs83m0k...).
    Everything here keys off its accessibility contract instead, which is the
    part that survives a redesign.
"""
from __future__ import annotations

import os
import subprocess
import time

import safety

# The desktop app's WebView2 debugging port. Distinct from browser.py's 9222 so
# Jarvis's Chrome and WhatsApp never fight over one endpoint.
PORT = int(os.environ.get("WHATSAPP_CDP_PORT", "9223"))
CDP = f"http://127.0.0.1:{PORT}"
APP_EXE = "WhatsApp.Root.exe"
READY_TIMEOUT = 45.0

# The package family name is stable across versions; only the version in the
# folder name changes. That version is EXACTLY what broke this: the old code
# hardcoded 2.2636 and the machine was on 2.2640, so it reported "not installed"
# while WhatsApp sat right there in the Start menu.
APP_PACKAGE = "5319275A.WhatsAppDesktop"
_APP_DIR_CACHE: str | None = None


def _resolve_app_dir() -> str:
    """Where WhatsApp Desktop actually is, resolved - never hardcoded.

    C:\\Program Files\\WindowsApps is a protected directory that an ordinary
    process cannot even LIST, so globbing it returns nothing (verified). But
    Get-AppxPackage reads the package registry, not the directory, and returns
    the real InstallLocation for whatever version is installed now. That is the
    one lookup that survives an update.

    Order: an explicit override in .env, then the live package location, then a
    best-effort guess so the error message at least names a plausible path."""
    global _APP_DIR_CACHE
    override = (os.environ.get("WHATSAPP_APP_DIR") or "").strip()
    if override:
        return override
    if _APP_DIR_CACHE:
        return _APP_DIR_CACHE
    try:
        ps = f"(Get-AppxPackage *{APP_PACKAGE}*).InstallLocation"
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True,
                           timeout=25, creationflags=0x08000000)
        loc = (r.stdout or "").strip().splitlines()
        if loc and loc[0].strip():
            _APP_DIR_CACHE = loc[0].strip()
            return _APP_DIR_CACHE
    except Exception:
        pass
    return rf"C:\Program Files\WindowsApps\{APP_PACKAGE}_x64__cv1g1gvanyjgm"


SEL = {
    # The search box's aria-label CHANGES once it has focus or content, so it
    # is matched by a chain, not one string. Relying on the label alone made
    # open() fail with "the search box is not on screen" while it was in fact
    # right there, holding leftover text from the previous call.
    "search": '[aria-label="Search or start a new chat"]',
    "search_alt": 'div[contenteditable="true"][data-tab="3"]',
    "search_any": '#side [role="textbox"]',
    "row": '#pane-side [role="row"]',
    "main": "#main",
    "composer": 'footer [contenteditable="true"][data-tab="10"]',
    "msg_row": '#main [role="row"]',
    "qr": "[data-ref]",
}

SEL = {
    # The search box's aria-label CHANGES once it has focus or content, so it
    # is matched by a chain, not one string. Relying on the label alone made
    # open() fail with "the search box is not on screen" while it was in fact
    # right there, holding leftover text from the previous call.
    "search": '[aria-label="Search or start a new chat"]',
    "search_alt": 'div[contenteditable="true"][data-tab="3"]',
    "search_any": '#side [role="textbox"]',
    "row": '#pane-side [role="row"]',
    "main": "#main",
    "composer": 'footer [contenteditable="true"][data-tab="10"]',
    "msg_row": '#main [role="row"]',
    "qr": "[data-ref]",
}


class NotRunning(Exception):
    """The app is not up with its debugging port open."""


class NotSignedIn(Exception):
    """The app is showing the QR screen."""


class WrongChat(Exception):
    """The message box named someone else. NOTHING was typed."""


# ─────────────────────────────────────────────────────────────────────────────
# Getting the app up with a port we can talk to
# ─────────────────────────────────────────────────────────────────────────────

def port_open(timeout: float = 1.5) -> bool:
    import socket
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout):
            return True
    except OSError:
        return False


def _is_running() -> bool:
    """Is WhatsApp up at all, with or without a debugging port?"""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {APP_EXE}"],
                             capture_output=True, text=True, timeout=10)
        return APP_EXE.lower() in (out.stdout or "").lower()
    except Exception:
        return False


def start_app(restart: bool = False) -> dict:
    """Launch WhatsApp with its WebView2 debugging port open.

    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS is passed to the CHILD PROCESS ONLY.
    Setting it as a persistent user variable would turn on a debugging port for
    every WebView2 app on the machine, forever, which is not a side effect
    Jarvis gets to have."""
    if port_open() and not restart:
        return {"ok": True, "status": "already_running"}

    # THE COMMON CASE, AND IT HAD TO BE HANDLED RATHER THAN EXPLAINED.
    # The Boss opens WhatsApp himself, from the taskbar, with no debugging
    # flag. Launching it again then just focuses that instance and the port
    # never appears - so an already-running app without a port gets restarted
    # WITH one. He loses nothing: it reopens signed in, on the same chats.
    running = _is_running()
    if restart or running:
        subprocess.run(["taskkill", "/F", "/IM", APP_EXE],
                       capture_output=True, check=False)
        time.sleep(2.5)

    exe = os.path.join(_resolve_app_dir(), APP_EXE)
    if not os.path.isfile(exe):
        raise NotRunning(
            f"WhatsApp Desktop was not found at {exe}. Install it from the "
            f"Microsoft Store, or if it is installed elsewhere set "
            f"WHATSAPP_APP_DIR in .env.")

    env = dict(os.environ)
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"--remote-debugging-port={PORT}"
    subprocess.Popen([exe], env=env, close_fds=True,
                     creationflags=0x08000000)          # CREATE_NO_WINDOW

    deadline = time.time() + 30
    while time.time() < deadline:
        if port_open():
            time.sleep(2)                                # let the page attach
            return {"ok": True, "status": "started", "port": PORT}
        time.sleep(1)
    raise NotRunning(
        f"WhatsApp started but never opened port {PORT}. It may have been "
        f"already running WITHOUT the debugging flag - close it from the "
        f"system tray and try again.")


# ─────────────────────────────────────────────────────────────────────────────
# One page handle, on one thread
# ─────────────────────────────────────────────────────────────────────────────

_pw = None
_browser = None


def _page():
    """The WhatsApp page inside the desktop app's WebView2.

    Playwright's sync API is greenlet-pinned, so this and everything that uses
    it run on browser.py's single executor thread - see the @pinned decorators
    below. A Page touched from a second thread raises, and that cost a whole
    debugging session once."""
    global _pw, _browser
    if not port_open():
        start_app()
    if _browser is None:
        # Borrow the ONE driver rather than starting a second on this thread.
        # Two sync Playwright drivers on one thread is an error, and the one
        # it raises blames asyncio, which sends you looking in the wrong place.
        _pw = browser.playwright_driver()
        _browser = _pw.chromium.connect_over_cdp(CDP)
    for ctx in _browser.contexts:
        for p in ctx.pages:
            if "web.whatsapp.com" in (p.url or ""):
                return p
    raise NotRunning("the app is open but its WhatsApp page is not loaded")


def _reset() -> None:
    global _pw, _browser
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None


import browser  # noqa: E402  (after _page, to avoid a circular import at load)


@browser.pinned
def up() -> bool:
    """Is the app open, loaded and signed in? The honest first question."""
    try:
        page = _page()
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline:
            if page.locator(SEL["qr"]).count():
                return False
            if _find_search(page) is not None:
                return True
            page.wait_for_timeout(500)
        return False
    except Exception:
        return False


def sign_in_hint() -> str:
    return ("WhatsApp Desktop is showing the QR screen. Scan it once from the "
            "phone - it is your own account and it stays linked.")


# ─────────────────────────────────────────────────────────────────────────────
# Addressing — the safety property
# ─────────────────────────────────────────────────────────────────────────────

def ensure_foreground() -> bool:
    """Put the WhatsApp window in front. Returns whether it worked.

    THIS IS NOT PIXEL AUTOMATION - no coordinate is read or clicked. It raises
    a window, exactly as hud_gl raises its own, and everything afterwards is
    still addressed by CSS selector.

    IT IS NECESSARY, AND FINDING OUT WHY TOOK THREE WRONG TURNS:

      - click() fails on a background window. Playwright's "stable" check
        compares the element's box across animation frames, and an occluded
        window renders none, so the check never passes. It then throws a
        timeout containing a dump of the element - which is how a wall of
        obfuscated CSS class names reached the Boss on Telegram.

      - fill() sets the value but WhatsApp ignores it. The search is a React
        controlled input, and even the native-setter trick leaves React's own
        query state empty: the search pane opens, the box shows the text, and
        the list stays unfiltered. Acting on that list opens the WRONG CHAT.

      - so the query has to arrive as real key events, and those need the
        window up. A person messaging someone brings WhatsApp up too.

    Windows refuses SetForegroundWindow to a process that does not already own
    the foreground; the documented workaround is a synthetic ALT, which marks
    this process as having had recent input."""
    import ctypes

    ps = ("$w = Get-Process WhatsApp.Root -EA SilentlyContinue | "
          "Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1; "
          "if ($w) { $w.MainWindowHandle }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                            "-Command", ps], capture_output=True, text=True,
                           timeout=20, creationflags=0x08000000)
        hwnd = int((r.stdout or "0").strip() or 0)
    except Exception:
        return False
    if not hwnd:
        return False
    try:
        u32 = ctypes.windll.user32
        u32.keybd_event(0x12, 0, 0, 0)        # ALT down
        u32.keybd_event(0x12, 0, 2, 0)        # ALT up
        u32.ShowWindow(hwnd, 9)               # SW_RESTORE, in case minimised
        u32.SetForegroundWindow(hwnd)
        u32.BringWindowToTop(hwnd)
        time.sleep(1.0)                       # let it paint before we click
        return True
    except Exception:
        return False


def _wait_until(page, js: str, timeout_ms: int = 6000, poll_ms: int = 120) -> bool:
    """Wait for a CONDITION, not for a duration. Returns whether it came true.

    §28: never wait unnecessarily. A flat wait_for_timeout(2000) pays two
    seconds every time even when the chat opened in two hundred milliseconds,
    and still gives up too early on the one slow load that mattered. This
    returns the moment the page is actually ready, and only spends the full
    timeout when something is genuinely wrong."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        try:
            if page.evaluate(js):
                return True
        except Exception:
            pass
        page.wait_for_timeout(poll_ms)
    return False


def _find_search(page):
    """The search box, whichever label it is wearing right now."""
    for key in ("search", "search_alt", "search_any"):
        loc = page.locator(SEL[key])
        if loc.count():
            return loc.first
    return None


def _search_box(page):
    box = _find_search(page)
    if box is None:
        raise NotRunning("the search box is not on screen - is WhatsApp loaded?")
    return box


def _rows(page):
    """Real conversations only.

    "Chats" and "Groups in common" are rows too, but they are SECTION HEADINGS
    and clicking one opens nothing. A real chat carries a span[title]. Missing
    this is why an early version clicked a heading and then reported success
    against a chat that was never opened."""
    return page.locator(SEL["row"]).filter(has=page.locator("span[title]"))


@browser.pinned
def open(contact: str) -> dict:
    """Open one chat and PROVE which one it is. Raises rather than guessing."""
    want = (contact or "").strip()
    if not want:
        raise ValueError("no contact given")

    page = _page()
    if page.locator(SEL["qr"]).count():
        raise NotSignedIn(sign_in_hint())

    # NOTHING HERE DEPENDS ON THE WINDOW BEING IN FRONT, and that is the whole
    # point. An earlier version used click() and keyboard.type(), which worked
    # only while WhatsApp happened to be the foreground window. In the
    # background, Playwright's actionability check never passed: the click sat
    # for six seconds and threw a timeout containing a dump of the element's
    # HTML - which is how forty obfuscated CSS class names ended up being sent
    # to the Boss on Telegram as the explanation for a failed message. Worse,
    # keyboard.type() silently went nowhere, so the chat list was NEVER
    # filtered and the code went on to open whatever chat sat at the top.
    #
    # fill() sets the value through the DOM with proper input events, and the
    # row is opened with a DOM .click() from evaluate(). Both work on a
    # background window, because neither involves a real pointer.
    # ALREADY THERE? Then do not search at all.
    #
    # The watcher calls open() to resolve the contact, then new_since() ->
    # history() -> open(), then send() -> open(): THREE searches for one
    # reply, each one typing into the search box and re-rendering the list.
    # The Boss watched it happen - "it searches the name three times then
    # replies". The message box already names its recipient, so when it names
    # the right one there is nothing to look up, and the guard is satisfied by
    # the same element it would have been satisfied by afterwards.
    comp = page.locator(SEL["composer"])
    if comp.count():
        current = (comp.first.get_attribute("aria-label") or "")
        current = current.replace("Type a message to", "").strip()
        header = _header_name(page)
        if (header and _matches(want, header)) or (current and _matches(want, current)):
            who = header or current
            return {"ok": True, "contact": who, "label": current,
                    "reused": True}

    # Start from a known state. A previous call leaves the search pane open
    # with its query still in it, and the first open() after that clicked a
    # result belonging to the OLD search. Escape closes the pane; the sidebar
    # comes back to the full list before anything is typed.
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    box = _search_box(page)

    # focus(), never click(). Playwright's click waits for the element to be
    # "stable" - it compares the box across animation frames - and a window
    # that is occluded or minimised renders none, so the wait can never end.
    # It then throws a timeout whose text contains a dump of the element, and
    # that dump is how forty obfuscated CSS class names were once sent to the
    # Boss on Telegram as the reason his message failed. focus() has no such
    # check, and the key events below are delivered over CDP to the focused
    # element rather than by the OS, so none of this needs the window in front.
    box.focus()
    page.keyboard.press("Control+A")        # the box keeps the last query
    page.keyboard.press("Delete")
    page.wait_for_timeout(300)
    page.keyboard.type(want, delay=30)      # REAL key events; React needs them
    # A FIXED SETTLE, THEN A CONDITION. Waiting purely on a condition was
    # tried and made this flaky: the results grid stays on screen holding the
    # PREVIOUS query's rows, so every "is it ready" test was already true
    # before React had re-rendered, and the code arrowed to an index computed
    # from stale names. React needs a moment it will not announce, so it gets
    # one - and then the condition below exits early when the page is quicker,
    # which is where the saving actually is.
    page.wait_for_timeout(1500)
    _wait_until(page,
                """() => {
                    const g = document.querySelector(
                        '[role="grid"][aria-label="Search results."]');
                    return !!g && g.querySelectorAll('[role="row"]').length > 0;
                }""",
                timeout_ms=4000)

    # WHICH ONE. Searching "clip tool" really does return BOTH "Clip Tool" and
    # "Kap Kut" - names that differ by one letter and an emoji. Taking the
    # first row would have messaged whichever WhatsApp happened to rank
    # higher. Ambiguity is a question, never a coin toss.
    # While searching, results live in a grid labelled "Search results." -
    # NOT in #pane-side, which keeps showing the unfiltered chat list. Reading
    # the wrong container is how "clip tool" resolved to a college group chat.
    # Only the FIRST span[title] in a row is the chat name; the second is the
    # message preview, and treating those as names made every row a candidate.
    names = page.evaluate(
        """() => {
            const g = document.querySelector('[role="grid"][aria-label="Search results."]')
                   || document.querySelector('#pane-side [role="grid"]')
                   || document.querySelector('#pane-side');
            if (!g) return [];
            return [...g.querySelectorAll('[role="row"]')].map(r => {
                const t = r.querySelector('span[title]');
                return t ? t.getAttribute('title') : null;
            }).filter(Boolean);
        }""")
    seen, cands = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            cands.append(n)

    if not cands:
        page.keyboard.press("Escape")
        raise ValueError(f"no WhatsApp chat matches {want!r}")

    # DO NOT TRUST THE RESULT LIST. WhatsApp sometimes leaves the full chat
    # list on screen under the "Search results." label - the pane is open but
    # the query has not been applied - and it also matches on message BODIES,
    # so a chat can appear because someone once typed the word. Either way the
    # rows are not all matches. Searching "98765 43210" returned twenty chats
    # including college groups; acting on that list means messaging a stranger.
    #
    # So the list is re-filtered here against what was actually asked for. A
    # result that does not contain the query in its NAME is not a candidate.
    asked = _norm(want)
    digits = "".join(ch for ch in want if ch.isdigit())
    kept = [n for n in cands
            if (asked and asked in _norm(n))
            or (len(digits) >= 6 and digits in "".join(
                ch for ch in n if ch.isdigit()))]
    if kept:
        cands = kept
    elif len(cands) > 3:
        page.keyboard.press("Escape")
        raise ValueError(
            f"searching {want!r} did not narrow anything down - no chat name "
            f"contains it. Nothing was sent. Check the name as it appears in "
            f"WhatsApp.")

    exact = [n for n in cands if _norm(n) == _norm(want)]
    if exact:
        cands = exact[:1]
    elif len(cands) > 1:
        page.keyboard.press("Escape")
        shown = ", ".join(repr(c) for c in cands[:6])
        raise WrongChat(
            f"{want!r} matches {len(cands)} chats ({shown}) - nothing was "
            f"sent. Which one?")

    # Open it by DOM click on the row that carries THAT EXACT TITLE - not by
    # position, and not by a text filter that an emoji in the name could break.
    # Open it with the ARROW KEYS, not a DOM click.
    #
    # element.click() from evaluate() does fire, and WhatsApp ignores it:
    # React binds its handlers through a synthetic event system on a specific
    # inner node, so a programmatic click on the row leaves the chat closed
    # while reporting that it worked. Arrow-then-Enter is WhatsApp's own
    # keyboard navigation, delivered over CDP, and it needs no window focus.
    try:
        idx = names.index(cands[0])
    except ValueError:
        page.keyboard.press("Escape")
        raise WrongChat(f"{cands[0]!r} left the results before it could open")

    for _ in range(idx + 1):
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(60)
    page.keyboard.press("Enter")
    # Same shape: let it open, then confirm it really did. The condition is
    # the MESSAGE BOX, because that is the element the guard reads and the one
    # the keystrokes land in - so there is no moment where it looks open and
    # is not.
    page.wait_for_timeout(1200)
    _wait_until(page,
                """() => !!document.querySelector(
                     'footer [contenteditable="true"][data-tab="10"]')""",
                timeout_ms=6000)

    if not page.locator(SEL["main"]).count():
        raise WrongChat(f"clicked a result for {want!r} but no chat opened")

    # THE GUARD. Confirm the chat that OPENED is the one asked for - but read
    # its HEADER name, not only the message box. A contact saved by NUMBER shows
    # "Type a message to +91 ..." in the box while the header (and the row we
    # picked) says the real name. Reading only the box rejected chats that had
    # opened perfectly: verified live on "Kap Cut", whose header says "Kap Cut"
    # but whose composer says a bare number.
    comp = page.locator(SEL["composer"])
    if not comp.count():
        raise WrongChat(f"{want!r} opened but there is no message box - "
                        f"nothing was typed")
    label = comp.first.get_attribute("aria-label") or ""
    box_who = label.replace("Type a message to", "").strip()
    header = _header_name(page)
    who = header or box_who or "(unnamed)"

    # The row was already chosen because its NAME matched the request; this just
    # confirms the open landed there. Either the header OR the box naming the
    # contact is proof enough, since a number-saved contact names it in only one.
    if not (_matches(want, header) or (box_who and _matches(want, box_who))):
        page.keyboard.press("Escape")
        raise WrongChat(
            f"asked for {want!r} but the open chat is {who!r} - nothing was "
            f"typed. Say which one you meant.")
    return {"ok": True, "contact": who, "label": label}


def _header_name(page) -> str:
    """The contact name shown in the OPEN chat's header, or ''.

    For most chats the header's span[title] is the name; for some it is absent
    and the first line of the header text carries it instead - verified live on
    "Kap Cut", which shows no span[title] but a header reading "Kap Cut" while
    its message box shows only the phone number."""
    try:
        return (page.evaluate("""() => {
            const h = document.querySelector('#main header');
            if (!h) return "";
            const t = h.querySelector('span[title]');
            if (t && t.getAttribute('title')) return t.getAttribute('title');
            return (h.innerText || '').split('\\n')[0].trim();
        }""") or "").strip()
    except Exception:
        return ""


def _norm(s: str) -> str:
    """Letters and digits only, lowercased.

    WhatsApp names carry emoji and directional marks - "Clip Tool🐸" is the same
    contact a person types as "clip tool". Comparing raw strings made every
    emoji-bearing name un-matchable, so an exact match could never win and
    everything fell through to the ambiguous branch."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _matches(want: str, got: str) -> bool:
    """Loose on formatting, strict on identity.

    Compared raw, "ClipTool" did not match "Clip Tool🐸" - a space and an emoji -
    so a chat that had opened correctly was rejected as the wrong one. Names
    are normalised to letters and digits before comparison; identity still has
    to match, formatting does not."""
    a, b = want.lower().strip(), got.lower().strip()
    if a in b or b in a:
        return True
    na, nb = _norm(want), _norm(got)
    if na and nb and (na in nb or nb in na):
        return True

    # BOTH sides must actually have digits. This line once read
    #     return bool(da) and len(da) >= 8 and (da in db or db in da)
    # and "Clip Tool" has no digits at all, so db was "" - and "" is a substring
    # of every string, so db in da was True and the guard passed. A message
    # addressed to 98765 43210 was delivered to Clip Tool. The empty string is
    # not a match for anything, and a comparison that can be satisfied by
    # absence is not a comparison.
    da = "".join(ch for ch in a if ch.isdigit())
    db = "".join(ch for ch in b if ch.isdigit())
    if len(da) < 8 or len(db) < 8:
        return False
    return da[-10:] == db[-10:] or da in db or db in da


# ─────────────────────────────────────────────────────────────────────────────
# Doing things
# ─────────────────────────────────────────────────────────────────────────────

@browser.pinned
def send(contact: str, text: str) -> dict:
    """Send as the Boss. The proof is the bubble read back off the screen."""
    if not (text or "").strip():
        raise ValueError("refusing to send an empty message")
    safety.money_check(text)

    # open() is @pinned too, and pinned() runs inline when it is already on the
    # browser thread - so this nests safely instead of deadlocking the
    # single-worker pool against itself.
    who = open(contact)
    page = _page()

    # focus(), not click() - same reason as the search box: click() waits for
    # the element to be "stable" across animation frames, which an occluded
    # window never provides, and then throws a timeout full of element HTML.
    box = page.locator(SEL["composer"]).first
    box.focus()
    page.wait_for_timeout(200)
    lines = (text or "").splitlines() or [text]
    for i, line in enumerate(lines):
        if i:
            page.keyboard.press("Shift+Enter")   # a bare Enter would SEND
        page.keyboard.type(line, delay=8)
    page.keyboard.press("Enter")

    # WAIT FOR THE MESSAGE TO APPEAR, do not assume the last row is ours.
    #
    # This used to read rows.last straight after Enter and call it proof. The
    # last row is often something else - a "Forwarded" label, a date divider,
    # an older bubble that has not re-rendered yet - so a perfectly good send
    # reported an echo of "Forwarded 9:49 am" and could not be verified.
    # Now it looks for the text itself, in the last few rows, for a few
    # seconds. Either it is on screen or this says plainly that it is not.
    needle = (text or "").strip().splitlines()[0][:60]
    echo, verified = "", False
    deadline = time.time() + 8
    while time.time() < deadline:
        page.wait_for_timeout(500)
        recent = page.evaluate(
            """() => [...document.querySelectorAll('#main [role="row"]')]
                 .slice(-6).map(r => (r.innerText || ''))""")
        for body in reversed(recent):
            if needle and needle in body:
                echo, verified = body.strip()[:200], True
                break
        if verified:
            break
    if not echo and recent:
        echo = (recent[-1] or "").strip()[:200]

    import state
    state.add_message(who["contact"], "out", text)
    return {"ok": True, "verified": verified, "contact": who["contact"],
            "echo": echo,
            "note": ("the message is on screen in the chat" if verified else
                     "SENT BUT NOT CONFIRMED - it was typed and Enter pressed, "
                     "but the text did not appear in the last rows. Say it is "
                     "unconfirmed rather than claiming it sent.")}


@browser.pinned
def history(contact: str, n: int = 20, fence: bool = True) -> list[dict]:
    """Recent messages, oldest last. Text is FENCED - it is data, not orders."""
    who = open(contact)
    page = _page()
    raw = page.evaluate(
        """(n) => {
            const rows = [...document.querySelectorAll('#main [role="row"]')].slice(-n);
            return rows.map(r => {
                const c = r.querySelector('.copyable-text');
                const tick = r.querySelector(
                    '[aria-label*="Delivered"], [aria-label*="Read"], [aria-label*="Sent"]');
                return {
                    pre: c ? (c.getAttribute('data-pre-plain-text') || '') : '',
                    text: (r.innerText || '').split('\\n')[0],
                    mine: !!tick,
                };
            });
        }""", n)
    out = []
    for m in raw:
        body = (m.get("text") or "").strip()
        if not body:
            continue
        sender = ""
        pre = m.get("pre") or ""
        if "] " in pre and pre.rstrip().endswith(":"):
            sender = pre.split("] ", 1)[1].rstrip(": ").strip()
        out.append({
            "from": "me" if m.get("mine") else (sender or who["contact"]),
            "mine": bool(m.get("mine")),
            "raw": body[:600],
            "text": safety.fence(body[:600],
                                 f"a WhatsApp message in {who['contact']}")
                    if fence else body[:600],
        })
    return out


@browser.pinned
def unread() -> list[dict]:
    """Chats with an unread badge. Read off the sidebar, no model call."""
    page = _page()
    return page.evaluate(
        """() => [...document.querySelectorAll('#pane-side [role="row"]')]
             .map(r => {
                 const t = r.querySelector('span[title]');
                 const b = r.querySelector('[aria-label*="unread" i]');
                 if (!t || !b) return null;
                 return {contact: t.getAttribute('title'),
                         count: b.getAttribute('aria-label')};
             }).filter(Boolean)""")


def new_since(contact: str, after_ts: float) -> list[dict]:
    """Incoming messages not seen before. What watch_chat polls.

    WhatsApp's DOM carries a time-of-day but not a full timestamp per row, so
    rather than invent one this compares against what is already stored. A
    message Jarvis has not recorded is new; one it has is not."""
    import state
    seen = {m["text"] for m in state.history(contact, 60)}
    now = time.time()
    if after_ts and now < after_ts:        # a clock that went backwards
        return []
    fresh = []
    # RAW, not fenced. What goes in the database is what the person actually
    # wrote; the fence is a wrapper for the model and belongs at the moment of
    # handing it over, not in storage. Storing it fenced meant every reply
    # re-fenced an already-fenced message and the warning banner itself became
    # most of the conversation history.
    for m in history(contact, 20, fence=False):
        if m["mine"] or m["text"] in seen:
            continue
        fresh.append({"text": m["text"], "ts": now, "jid": contact})
    return fresh


# ─────────────────────────────────────────────────────────────────────────────
# NOT AVAILABLE — stated, not silently missing
# ─────────────────────────────────────────────────────────────────────────────

def call(contact: str):
    """Placing a WhatsApp call is NOT POSSIBLE from here.

    The call button opens a native WebRTC session needing a microphone and a
    real user gesture, and it could not carry audio to a phone line anyway.
    lib.phone.call_me is a real carrier call over Twilio and already works."""
    raise NotImplementedError(
        "Jarvis cannot place a WhatsApp call. Say so plainly - do not claim a "
        "call was made. A normal phone call over Twilio is available instead.")
