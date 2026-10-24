"""
browser.py — driving the user's real Chrome through CDP.

WHY THIS EXISTS, from a live failure:
    Asked to find OpenAI emails from the last 7 days, Jarvis pressed ctrl+f and
    typed "openai gmails from past 7 days" into the BROWSER'S FIND BAR. The find
    box showed 0/0. It repeated that for six minutes.

    Two mistakes, both structural rather than careless:
      1. ctrl+f is the browser's find-in-page. Gmail's own search is a different
         box entirely. A keyboard shortcut that is right for WhatsApp Desktop is
         wrong for a web app, and nothing about an image of it tells you which.
      2. Search boxes take operators, not prose. "from:openai newer_than:7d"
         finds things; an English sentence finds nothing.

    The deeper problem: for anything in a browser, pixels-plus-keyboard is
    the wrong instrument. You are guessing at a UI when the page will simply tell
    you what is on it. This is established practice — browser-use moved off
    pixel heuristics onto raw CDP for exactly this reason.

WHAT THIS DOES INSTEAD
    Attaches to the user's actual running Chrome over the DevTools protocol, with
    their real logins intact. It can then list tabs, navigate by URL, read the
    page's text, click a real element by its text, and fill real fields. No pixel
    coordinates, no races for the foreground, no find-bar confusion.

THE PROFILE PROBLEM, AND WHY THIS LOOKED BROKEN FOR HOURS
    Chrome must be started with --remote-debugging-port or nothing here can
    attach. The obvious implementation - kill Chrome, relaunch it with the flag
    on the user's own profile - is what this did, and since Chrome 136 it
    CANNOT WORK. Google now refuses to open a debugging port when the profile is
    the default user-data-dir, deliberately, because an open CDP port on your
    signed-in profile is a cookie-theft primitive.

    Chrome does not complain. It launches, ignores the flag, and exits 0. So the
    owner saw: "Chrome restarted, but its debugging port still did not open",
    over and over, with Chrome visibly restarting each time. Verified here on
    Chrome 153: the same launch with a SEPARATE user-data-dir opens the port in
    under a second.

    So Jarvis gets its own Chrome profile, and the owner's Chrome is never
    touched again - not killed, not restarted, not interfered with. The cost is
    that the automation profile starts signed out, which is why seed_profile()
    copies the cookies across once.
"""
from __future__ import annotations

import atexit
import functools
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx

DEBUG_PORT = 9222
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
# The owner's real Chrome profile. READ ONLY, and only to copy a login out of.
# Never passed to --user-data-dir: see the note at the top of this file.
REAL_PROFILE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data")

# Jarvis's own Chrome profile. Persistent, so a site signed into once stays
# signed in, and separate, which is the only way Chrome will open the port.
PROFILE_DIR = os.path.expandvars(r"%LOCALAPPDATA%\Jarvis\ChromeProfile")

# Files worth copying out of the real profile so the automation browser starts
# signed in. Cookies is the one that matters; the rest make it feel like the
# owner's browser rather than a stranger's. Caches are skipped deliberately -
# they are most of the gigabytes and none of the value.
SEED_FILES = [
    ("Local State", "Local State"),                  # the cookie encryption key
    (r"Default\Cookies", r"Default\Network\Cookies"),
    (r"Default\Network\Cookies", r"Default\Network\Cookies"),
    (r"Default\Login Data", r"Default\Login Data"),
    (r"Default\Web Data", r"Default\Web Data"),
    (r"Default\Bookmarks", r"Default\Bookmarks"),
    (r"Default\Preferences", r"Default\Preferences"),
]

# Directories, copied whole. A site's LOGIN often is not in the cookie jar at
# all: WhatsApp Web keeps its linked-device keys in IndexedDB, so seeding
# cookies alone lands on a QR screen. These are best effort like everything
# else here - Chrome holds leveldb files open, and a locked file is skipped.
SEED_DIRS = [
    r"Default\Local Storage\leveldb",
    r"Default\IndexedDB\https_web.whatsapp.com_0.indexeddb.leveldb",
    r"Default\Session Storage",
]

CREATE_NO_WINDOW = 0x08000000

_pw: Any = None          # playwright driver
_browser: Any = None     # CDP-attached browser


# ─────────────────────────────────────────────────────────────────────────────
# ONE THREAD OWNS THE BROWSER
#
# Playwright's SYNC api is pinned to the thread (really the greenlet) that
# created the driver. Touch it from anywhere else and it fails with
#
#     Expected: <greenlet.greenlet object ... started main>
#
# which is exactly what appeared once the agent started driving Chrome, because
# every tool is dispatched through asyncio.to_thread - and to_thread is a thread
# POOL. The driver got created on whichever worker ran the first browser call,
# and the second call landed on a different worker.
#
# Same shape as brain.com_executor(), and for the same reason: some libraries
# have thread affinity, so give them one thread and route everything to it. A
# single worker also serialises browser work, which is correct anyway - two
# concurrent clicks on one page is not a thing anyone wants.
# ─────────────────────────────────────────────────────────────────────────────

_THREAD_NAME = "cdp"
_pool: Any = None
_pool_lock = threading.Lock()


def _executor():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=1,
                                           thread_name_prefix=_THREAD_NAME)
    return _pool


def pinned(fn: Callable) -> Callable:
    """Run `fn` on the browser thread, whoever calls it.

    Already on that thread (one browser function calling another) means running
    inline - submitting would deadlock a single-worker pool against itself."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if threading.current_thread().name.startswith(_THREAD_NAME):
            return fn(*args, **kwargs)
        return _executor().submit(fn, *args, **kwargs).result(timeout=180)
    return wrapper


@pinned
def on_page(fn: Callable, url_fragment: str = ""):
    """Run `fn(page)` on the browser thread and return a PLAIN value.

    PINNING THE FUNCTIONS WAS NOT ENOUGH. Playwright's objects carry the same
    thread affinity as the driver, so a Page handed back to a caller is a
    landmine: the call that returned it succeeds, and the first method invoked
    on it fails with "greenlet.error: Cannot switch to a different thread".

    So the Page never leaves. Callers send a function IN, and get data OUT:

        title = on_page(lambda p: p.title())

    Anything that needs raw page access - a test, a recipe, a one-off read -
    goes through here rather than reaching for _active_page()."""
    page = page_for(url_fragment) if url_fragment else _active_page()
    if page is None:
        return None
    return fn(page)


# ─────────────────────────────────────────────────────────────────────────────
# Attaching
# ─────────────────────────────────────────────────────────────────────────────

def debug_port_open(port: int | None = None, timeout: float = 1.5) -> bool:
    # port resolved at CALL time, not def time: a default of DEBUG_PORT would be
    # frozen at import and ignore any later change to the module constant.
    port = DEBUG_PORT if port is None else port
    try:
        httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout)
        return True
    except Exception:
        return False


def chrome_exe() -> str | None:
    return next((p for p in CHROME_PATHS if os.path.exists(p)), None)


def chrome_running() -> bool:
    try:
        import psutil
        return any("chrome.exe" == (p.info.get("name") or "").lower()
                   for p in psutil.process_iter(["name"]))
    except Exception:
        return False


def seed_profile(force: bool = False) -> dict:
    """Copy the owner's logins into Jarvis's profile, once.

    The automation profile has to be separate (Chrome will not open a debugging
    port on the default one), which means it starts signed out of everything.
    Copying the cookie database across makes it the owner's browser rather than
    a stranger's - same Windows user, so the DPAPI-encrypted cookies decrypt
    normally.

    BEST EFFORT BY DESIGN. Chrome holds some of these files open while it runs,
    and a locked file is a skipped file, not a failure: a profile with no
    cookies still drives a page perfectly, it just needs a sign-in. Nothing here
    writes to the real profile, and nothing is deleted."""
    made = os.path.isdir(PROFILE_DIR)
    if made and not force:
        return {"ok": True, "status": "already_seeded"}

    import shutil
    copied, skipped = [], []
    for src_rel, dst_rel in SEED_FILES:
        src = os.path.join(REAL_PROFILE_DIR, src_rel)
        dst = os.path.join(PROFILE_DIR, dst_rel)
        if not os.path.isfile(src):
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(src_rel)
        except Exception as e:
            skipped.append(f"{src_rel} ({type(e).__name__})")
    for rel in SEED_DIRS:
        src = os.path.join(REAL_PROFILE_DIR, rel)
        dst = os.path.join(PROFILE_DIR, rel)
        if not os.path.isdir(src):
            continue
        try:
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore_dangling_symlinks=True)
            copied.append(rel + "\\")
        except Exception as e:
            skipped.append(f"{rel} ({type(e).__name__})")

    return {"ok": True, "copied": copied, "skipped": skipped,
            "profile": PROFILE_DIR}


def _kill_profile_chrome() -> int:
    """Kill any Chrome running on JARVIS'S profile - never the owner's.

    Matched by the profile path in the command line, so the owner's Chrome,
    which runs on the default User Data dir, is never a candidate. Only called
    when the debug port is already known to be closed, so anything matched is a
    port-less instance that would otherwise block a fresh launch."""
    killed = 0
    try:
        import psutil
        target = PROFILE_DIR.lower()
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info.get("name") or "").lower() != "chrome.exe":
                continue
            cmd = " ".join(p.info.get("cmdline") or []).lower()
            if target in cmd:
                try:
                    p.kill()
                    killed += 1
                except Exception:
                    pass
    except Exception:
        pass
    if killed:
        time.sleep(1.0)          # let the lock clear before relaunching
    return killed


def start_chrome_with_debugging(restart: bool = False) -> dict:
    """Open Jarvis's own Chrome, with the debugging port live.

    THE OWNER'S CHROME IS NEVER TOUCHED. It used to be killed and relaunched on
    the default profile, which Chrome 136+ silently refuses to open a port for -
    so the owner watched their browser die repeatedly and still got "the
    debugging port did not open". Jarvis now runs a second Chrome on its own
    profile instead: theirs keeps its tabs, its session and its history.

    `restart` is kept in the signature because callers and the approval flow
    pass it, but there is nothing left to restart, so nothing needs approving."""
    exe = chrome_exe()
    if not exe:
        return {"ok": False, "error": "Chrome not found in the usual locations"}

    if debug_port_open():
        return {"ok": True, "port": DEBUG_PORT, "already": True,
                "spoken": "Browser control is already on."}

    # A PREVIOUS LAUNCH THAT DIED can leave a Jarvis Chrome running on this
    # profile WITHOUT a debug port. It then holds the profile's SingletonLock,
    # so the next launch just HANDS OFF to it and never opens a port either -
    # which is exactly the "debug port never opened" failure, verified live on
    # the owner's machine. The port is not open (checked above), so any Jarvis
    # Chrome still running is port-less and useless: clear it before launching.
    # Matched by the profile path, so the owner's own Chrome is never touched.
    _kill_profile_chrome()

    seeded = seed_profile()
    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
        subprocess.Popen(
            [exe,
             f"--remote-debugging-port={DEBUG_PORT}",
             f"--user-data-dir={PROFILE_DIR}",
             # GO STRAIGHT INTO A PROFILE, DO NOT SHOW THE PICKER.
             #
             # Found by testing, and it was self-inflicted: seed_profile copies
             # `Local State` across for the cookie key, and that file also
             # carries the owner's LIST of Chrome profiles. Chrome saw several
             # and helpfully opened "Who's using Chrome?" instead of a window.
             #
             # The failure this produced looked nothing like its cause. The port
             # opened fine, /json/version answered - and the only CDP target was
             # chrome://profile-picker/, which has no browser context, so every
             # connect died with "Browser context management is not supported".
             "--profile-directory=Default",
             # A first-run bubble or a default-browser prompt sits on top of the
             # page and swallows the first click of every session.
             "--no-first-run",
             "--no-default-browser-check",
             "--disable-session-crashed-bubble",
             # A real page, so there is always a normal target to attach to.
             # Launching with nothing can leave only internal targets, which is
             # the same dead end by a different route.
             "about:blank",
             ],
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    for _ in range(40):
        if debug_port_open():
            return {"ok": True, "port": DEBUG_PORT, "profile": PROFILE_DIR,
                    "seeded": seeded.get("copied", []),
                    "spoken": "Browser control is on."}
        time.sleep(0.25)
    return {"ok": False,
            "error": ("Chrome started but the debug port never opened. Another "
                      "process may be holding port "
                      f"{DEBUG_PORT}, or Chrome is blocked from listening."),
            "spoken": "Chrome started but would not accept control."}


@pinned
def _drop_connection() -> None:
    """Forget the cached CDP connection so the next call reattaches."""
    global _browser
    try:
        if _browser is not None:
            _browser.close()
    except Exception:
        pass
    _browser = None


@pinned
def shutdown() -> None:
    """Close the CDP link and stop the Playwright driver.

    WITHOUT THIS, EVERY EXIT IS AN ERROR. sync_playwright().start() spawns a
    Node process, and nothing ever stopped it - so when Python exited while the
    driver still had a message in flight, Node died face-up with

        Error: EOF: end of file, write ... Unhandled 'error' event

    and a twenty-line stack trace. Nothing was actually broken, which is the
    problem: a crash printed on every clean shutdown teaches you to ignore
    crashes.

    Runs on the browser thread like everything else - the driver belongs to that
    thread and stopping it from another is the same affinity bug in reverse."""
    global _pw
    _drop_connection()
    try:
        if _pw is not None:
            _pw.stop()
    except Exception:
        pass
    _pw = None


def _at_exit() -> None:
    # Only if the driver was ever started: importing browser.py must not cost a
    # thread, and atexit fires whether or not Chrome was used at all.
    if _pw is None and _pool is None:
        return
    try:
        shutdown()
    except Exception:
        pass
    try:
        if _pool is not None:
            _pool.shutdown(wait=False)
    except Exception:
        pass


atexit.register(_at_exit)


def _connect(fresh: bool = False):
    """Attach to the running Chrome. Cached — reconnecting per call is slow.

    THE CACHE HAS TO BE ABLE TO NOTICE IT IS DEAD. Checking `.contexts` was not
    enough: a connection whose last tab has been closed still answers that
    happily, and then every real call fails with

        TargetClosedError: ... Target page, context or browser has been closed

    forever, because nothing ever invalidated the cache. A monitor that hit that
    would keep reporting failures until Jarvis was restarted. Probing for a
    usable page is the check that actually distinguishes the two states."""
    global _pw, _browser
    if fresh:
        _drop_connection()

    if _browser is not None:
        try:
            ctxs = _browser.contexts
            # Touch something real. `.contexts` alone answers on a dead link.
            _ = [c.pages for c in ctxs]
            return _browser
        except Exception:
            _drop_connection()

    from playwright.sync_api import sync_playwright
    if _pw is None:
        _pw = sync_playwright().start()
    _browser = _pw.chromium.connect_over_cdp(f"http://127.0.0.1:{DEBUG_PORT}")
    return _browser


@pinned
def ensure(auto_restart: bool = False) -> dict:
    if debug_port_open():
        return {"ok": True, "port": DEBUG_PORT}
    return start_chrome_with_debugging(restart=auto_restart)


@pinned
def _pages() -> list:
    b = _connect()
    out = []
    for ctx in b.contexts:
        out.extend(ctx.pages)
    return out


# The HUD is itself a Chrome window. Navigating it would replace Jarvis's own
# face with Gmail, so it is excluded from every page the browser tools act on.
HUD_MARKERS = ("127.0.0.1:5051", "localhost:5051")


def _is_hud(url: str) -> bool:
    return any(m in (url or "") for m in HUD_MARKERS)


@pinned
def _active_page():
    """Pick the page to act on: the last real one the user has been working in.

    Skips blank/internal pages AND the Jarvis HUD — in fullscreen mode the HUD is
    a Chrome window like any other, and without this check browser_open would
    navigate Jarvis's own interface away."""
    pages = [p for p in _pages()
             if not p.url.startswith(("about:", "chrome://")) and not _is_hud(p.url)]
    if pages:
        return pages[-1]
    real = [p for p in _pages() if not _is_hud(p.url)]
    return real[0] if real else None


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

@pinned
def tabs() -> dict:
    st = ensure()
    if not st.get("ok"):
        return st
    try:
        items = [{"title": p.title()[:90], "url": p.url[:140]}
                 for p in _pages() if not _is_hud(p.url)]
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "count": len(items), "tabs": items,
            "spoken": f"{len(items)} tabs open."}


@pinned
def goto(url: str, new_tab: bool = False) -> dict:
    """Navigate. Reuses the current tab unless asked otherwise — the user
    complained about a second browser appearing, and they were right."""
    st = ensure()
    if not st.get("ok"):
        return st
    opened_here = None
    try:
        b = _connect()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "spoken": "I could not reach the browser."}

    try:
        if new_tab or _active_page() is None:
            # REUSE A BLANK TAB BEFORE MAKING ANOTHER ONE. Chrome is launched
            # with an about:blank page, and every failed navigation used to
            # leave its own behind - four blank tabs after three checks, and
            # _active_page() picks the LAST real tab, so the litter starts
            # winning.
            blanks = [p for p in _pages() if p.url in ("", "about:blank")]
            if blanks:
                page = blanks[0]
            else:
                ctx = b.contexts[0] if b.contexts else b.new_context()
                page = ctx.new_page()
                opened_here = page
        else:
            page = _active_page()

        # One retry. A page created a moment ago can answer Page.navigate with
        # a protocol error while it is still being set up; the same call a
        # breath later is fine. Observed live, once, and not reproducible - so
        # retry rather than build a theory about it.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            time.sleep(0.6)
            page.goto(url, wait_until="domcontentloaded", timeout=30000)

        page.bring_to_front()
        # Wait for the page to SETTLE, not for a fixed second. A fast page was
        # paying a full second it did not need, and a slow one was being read
        # before it had rendered - the fixed timer was wrong in both
        # directions at once.
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass          # already idle, or still loading; either is readable
        return {"ok": True, "url": page.url[:140], "title": page.title()[:90],
                "spoken": "Opened."}
    except Exception as e:
        # Do not leave a blank tab behind on the way out.
        if opened_here is not None:
            try:
                opened_here.close()
            except Exception:
                pass

        # CLOSING THE LAST TAB KILLS THE CONNECTION, not the browser. Chrome is
        # still there; the CDP link is stale. Reconnect once and try again,
        # because the alternative is a monitor that fails forever after the
        # owner tidies up their tabs.
        if "TargetClosed" in type(e).__name__ or "has been closed" in str(e):
            try:
                b = _connect(fresh=True)
                ctx = b.contexts[0] if b.contexts else b.new_context()
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.bring_to_front()
                time.sleep(1.0)
                return {"ok": True, "url": page.url[:140],
                        "title": page.title()[:90], "reconnected": True,
                        "spoken": "Opened."}
            except Exception as e2:
                e = e2

        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "spoken": "I could not open that page."}


@pinned
def read(selector: str = "", max_chars: int = 4000) -> dict:
    """Read the page's visible text — what is actually there, not what a
    an image of the page suggests."""
    st = ensure()
    if not st.get("ok"):
        return st
    page = _active_page()
    if page is None:
        return {"ok": False, "error": "no open tab"}
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        # A SELECTOR THAT MATCHES NOTHING SHOULD FAIL FAST. Eight seconds is
        # the right patience for a page still loading and the wrong patience
        # for a typo - and on a call it is eight seconds of dead air before an
        # error. The whole-body read keeps a longer budget, because that one is
        # usually waiting on the page itself.
        text = (page.locator(selector).first.inner_text(timeout=2500)
                if selector else page.locator("body").inner_text(timeout=10000))
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "spoken": "I could not read that page."}
    text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
    return {"ok": True, "title": page.title()[:90], "url": page.url[:140],
            "text": text[:max_chars],
            "truncated": len(text) > max_chars}


@pinned
def page_for(url_fragment: str):
    """The tab whose URL contains this fragment, or None.

    A mission must read ITS page, not whichever tab happens to be in front.
    _active_page() is right for "do this now" and wrong for "keep watching
    that": the moment you switched tabs, a watch built on it would silently
    start reporting the price of something else.

    A SUBSTRING MATCH IS NOT ENOUGH, and the failure is the sort that looks
    like a correct answer. Asked for "youtube.com/watch" it returned the
    MUSIC.youtube.com tab, because that URL contains that substring too - so a
    question about one open tab was answered with another tab's queue, in the
    right shape, with no error anywhere.

    Host is therefore matched on a boundary: a fragment naming a host matches
    that host or a subdomain of it, never a different host that merely ends
    the same way. Exact URL first, then host, then substring as a last resort.
    """
    from urllib.parse import urlparse

    frag = (url_fragment or "").strip().lower()
    if not frag:
        return None

    # A PORT IS PART OF THE ADDRESS. "localhost:5173/app" named a host that
    # urlparse reports without its port, so no fragment naming a dev server
    # could ever match by host and every local port fell back to a substring
    # scan - which is how the wrong-tab class of bug got in to begin with.
    first = frag.split("/")[0]
    host_part, _, want_port = first.partition(":")
    if "." in host_part or host_part == "localhost":
        want_host, want_path = host_part, frag[len(first):]
    else:
        want_host = want_path = want_port = ""

    candidates = []
    for p in _pages():
        try:
            url = (p.url or "").lower()
            if _is_hud(url):
                continue
            candidates.append((p, url))
        except Exception:
            continue

    def bare(host: str) -> str:
        return host[4:] if host.startswith("www.") else host

    def rank(url: str) -> int:
        """0 = the host asked for, 1 = a subdomain of it, 2 = no match.

        EXACT BEATS SUBDOMAIN, and that ordering is the fix. "youtube.com"
        means www.youtube.com; music.youtube.com is a different application
        that happens to sit under the same domain, and returning it for a
        question about the other one produced a real, plausible-looking wrong
        answer - the music queue, presented as the video tab's queue."""
        u = urlparse(url)
        if want_port and str(u.port or "") != want_port:
            return 2
        host = bare((u.hostname or "").lower())
        if host == bare(want_host):
            return 0
        if host.endswith("." + bare(want_host)):
            return 1
        return 2

    if want_host:
        def path_ok(url: str) -> bool:
            u = urlparse(url)
            return not want_path or want_path in (u.path + ("?" + (u.query or "")))

        for tier in (0, 1):                # exact host first, then subdomains
            for p, url in candidates:      # with the path fragment honoured
                if rank(url) == tier and path_ok(url):
                    return p
            for p, url in candidates:      # then host alone at that tier
                if rank(url) == tier:
                    return p
        return None

    for p, url in candidates:              # no host in the fragment: substring
        if frag in url:
            return p
    return None


@pinned
def probe(url: str, selector: str = "", max_chars: int = 400,
          open_if_missing: bool = True) -> dict:
    """Read one value from one page, cheaply and repeatedly.

    THE WHOLE POINT IS THAT THIS COSTS NOTHING. A mission that checks every 30
    seconds is 2,880 checks a day; routing each through the agent would mean
    2,880 model calls to re-read a number that is sitting in the DOM. The model
    is used ONCE, when the mission is created, to work out which selector holds
    the value. After that this reads it directly, forever, for free.

    Deliberately does NOT call bring_to_front(). Reading over CDP needs no
    the foreground, and a watch that stole it every 30 seconds would make the
    machine unusable."""
    st = ensure()
    if not st.get("ok"):
        return st

    key = (url or "").split("://")[-1].split("?")[0][:60]
    page = page_for(key) or page_for(url)
    if page is None:
        if not open_if_missing:
            return {"ok": False, "error": "that page is not open", "closed": True}
        # A WATCH IS OFTEN CREATED WITH A BARE HOST - "watch example.com" -
        # and a bare host is not a URL. Navigating to one lands on a search
        # page or fails outright, so the monitor could never reopen its own
        # page after the tab was closed.
        target = url if "://" in url else f"https://{url}"
        opened = goto(target, new_tab=True)
        if not opened.get("ok"):
            return opened
        page = page_for(key) or _active_page()
        if page is None:
            return {"ok": False, "error": "could not open the page"}

    try:
        node = page.locator(selector).first if selector else page.locator("body")
        text = node.inner_text(timeout=6000)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:120]}",
                "url": page.url[:140],
                "hint": "the selector no longer matches - the page layout "
                        "probably changed"}

    text = " ".join(text.split())[:max_chars]
    return {"ok": True, "text": text, "url": page.url[:140],
            "title": page.title()[:90]}


@pinned
def click_text(text: str, exact: bool = False) -> dict:
    """Click a real element by its visible text. No coordinates involved.

    Several strategies, because one is not enough: a link may be a <a>, a
    <button>, an aria-labelled div, or text with a trailing ellipsis that breaks
    an exact match. Failing on the first miss is what sends an agent back to
    guessing pixels."""
    st = ensure()
    if not st.get("ok"):
        return st
    page = _active_page()
    if page is None:
        return {"ok": False, "error": "no open tab"}

    attempts = [
        ("role=link", lambda: page.get_by_role("link", name=text, exact=exact).first),
        ("role=button", lambda: page.get_by_role("button", name=text, exact=exact).first),
        ("text", lambda: page.get_by_text(text, exact=exact).first),
        ("aria-label", lambda: page.locator(f'[aria-label*="{text}"]').first),
        ("substring", lambda: page.locator(f':text("{text}")').first),
    ]
    errors = []
    before = page.url
    for how, build in attempts:
        try:
            loc = build()
            # Visible, or it is not the thing the user can see and meant. A
            # hidden element that happens to match first is exactly how fill()
            # used to spend eight seconds timing out on a search box.
            if not loc.is_visible(timeout=1500):
                errors.append(f"{how}: not visible")
                continue
            loc.scroll_into_view_if_needed(timeout=3000)
            loc.click(timeout=4000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass          # an in-page click may not navigate at all
            return {"ok": True, "clicked": text, "matched_by": how,
                    "url": page.url[:140], "changed_page": page.url != before,
                    "spoken": f"Clicked {text}."}
        except Exception as e:
            errors.append(f"{how}: {type(e).__name__}")

    # WHAT IS ACTUALLY THERE, not just what is not. "I could not find it" sends
    # the model guessing; a list of the real link text ends the guessing in one
    # turn - and the words on a page change under you (example.com's own link
    # went from "More information..." to "Learn more").
    nearby = []
    try:
        nearby = [t.strip()[:40] for t in page.eval_on_selector_all(
            "a, button, [role=button]",
            "(els) => els.filter(e => e.offsetWidth || e.offsetHeight)"
            ".map(e => e.innerText).filter(Boolean)")][:12]
    except Exception:
        pass

    return {"ok": False,
            "error": f"no clickable element matching {text!r} ({'; '.join(errors[:3])})",
            "clickable_here": nearby,
            "hint": "These are the clickable items actually on the page - use "
                    "one of them, or call browser_read to see more.",
            "spoken": f"I could not find {text} on the page."}


def _first_usable(page, selector: str, limit: int = 12):
    """The first element matching `selector` that a person could actually type in.

    `.first` IS NOT GOOD ENOUGH, and this is not a corner case. DuckDuckGo's
    search box is a <textarea name="q">, and the page also carries five HIDDEN
    <input> elements. `page.locator("input[name=q]").first` therefore resolved to
    something invisible, and the click sat there until it timed out - eight
    seconds to fail at typing into a search box.

    Visible and enabled, or it is not the field the user means."""
    try:
        loc = page.locator(selector)
        for i in range(min(loc.count(), limit)):
            el = loc.nth(i)
            try:
                if el.is_visible() and el.is_enabled():
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


@pinned
def fill(selector: str, value: str, submit: bool = False) -> dict:
    """Type into a field. `selector` is a HINT, not a requirement.

    Several strategies, for the same reason click_text has several: a search box
    may be an input, a textarea, a contenteditable div, or labelled only by an
    aria attribute. Failing on the first miss is what sends an agent back to
    guessing at pixels and pressing ctrl+f."""
    st = ensure()
    if not st.get("ok"):
        return st
    page = _active_page()
    if page is None:
        return {"ok": False, "error": "no open tab"}

    # An input[...] hint should also try the textarea of the same name, because
    # that is the single most common way this goes wrong on real sites.
    hints = [selector]
    if selector.startswith("input["):
        hints.append("textarea[" + selector.split("[", 1)[1])
    elif selector.startswith("textarea["):
        hints.append("input[" + selector.split("[", 1)[1])
    hints.append(f"{selector}, input, textarea, [contenteditable=true]")

    attempts: list[tuple[str, Any]] = []
    for h in hints:
        attempts.append((f"selector {h[:40]}", lambda h=h: _first_usable(page, h)))
    attempts += [
        ("role=searchbox", lambda: page.get_by_role("searchbox").first),
        ("role=textbox", lambda: page.get_by_role("textbox").first),
        ("role=combobox", lambda: page.get_by_role("combobox").first),
    ]

    errors = []
    for how, build in attempts:
        try:
            el = build()
            if el is None:
                errors.append(f"{how}: nothing visible")
                continue
            el.click(timeout=4000)
            el.fill(value, timeout=4000)
            if submit:
                el.press("Enter")
                try:
                    page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass      # a client-side form may never go to the network
            return {"ok": True, "filled": selector, "matched_by": how,
                    "value": value[:60], "url": page.url[:140],
                    "spoken": "Done."}
        except Exception as e:
            errors.append(f"{how}: {type(e).__name__}")

    return {"ok": False,
            "error": (f"no usable field matching {selector!r} "
                      f"({'; '.join(errors[:3])})"),
            "hint": "Call browser_read to see the page, then use a selector "
                    "from what is actually there.",
            "spoken": "I could not find that field on the page."}


# ─────────────────────────────────────────────────────────────────────────────
# Gmail
# ─────────────────────────────────────────────────────────────────────────────
# Gmail search is a URL, which removes every failure from the live transcript:
# no find-bar confusion, no race for the foreground, and operators not prose.

GMAIL_OPERATORS = {
    "from": "from:openai.com",
    "unread": "is:unread",
    "recent": "newer_than:7d",
    "subject": "subject:interview",
    "attachment": "has:attachment",
}


@pinned
def gmail_search(query: str, max_chars: int = 4000) -> dict:
    """Search Gmail properly and read the results.

    `query` uses Gmail operators, e.g.
        from:openai newer_than:7d
        subject:interview is:unread
        from:(naukri OR linkedin) newer_than:3d
    Plain words work too, but operators are what make a date filter possible.
    """
    from urllib.parse import quote
    url = f"https://mail.google.com/mail/u/0/#search/{quote(query, safe=':()+')}"
    nav = goto(url)
    if not nav.get("ok"):
        return nav
    time.sleep(2.5)                     # Gmail renders results client-side
    page = _active_page()
    try:
        page.wait_for_selector("table[role='grid'], div[role='main']", timeout=12000)
        time.sleep(0.8)
        rows = page.locator("tr.zA")
        n = rows.count()
        items = []
        for i in range(min(n, 25)):
            try:
                r = rows.nth(i)
                sender = r.locator("td .yW span").first.inner_text(timeout=1500)
                subject = r.locator("td .y6 span").first.inner_text(timeout=1500)
                date = r.locator("td.xW span").first.inner_text(timeout=1500)
                unread = "zE" in (r.get_attribute("class") or "")
                items.append({"from": sender.strip()[:60],
                              "subject": subject.strip()[:90],
                              "date": date.strip()[:20],
                              "unread": unread})
            except Exception:
                continue
        if items:
            unread_n = sum(1 for i in items if i["unread"])
            return {
                "ok": True, "query": query, "count": len(items),
                "unread_count": unread_n,
                "results": items,
                "spoken": (f"{len(items)} results for that search"
                           + (f", {unread_n} unread." if unread_n else ".")),
            }
    except Exception:
        pass

    # Rendering changed or nothing matched — fall back to the page text so the
    # agent still has something real rather than a guess.
    body = read(max_chars=max_chars)
    body["query"] = query
    body["note"] = ("Could not parse individual rows; this is the raw page text. "
                    "If it says no results, the search genuinely found nothing.")
    return body


# ─────────────────────────────────────────────────────────────────────────────
# Web search
# ─────────────────────────────────────────────────────────────────────────────
# AGENTS.md §6 listed `answer_question` as a read-only tool and it was never
# built, so Jarvis could open a page but not FIND one. For a student or a
# developer that is the single most common action there is: look something up.
#
# Deliberately httpx + BeautifulSoup rather than the CDP browser. Search must
# work for SCHEDULED jobs too, and those run with no browser attached and nobody
# watching; requiring a Chrome restart to answer a question would be absurd.

_DDG = "https://html.duckduckgo.com/html/"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


@pinned
def search(query: str, limit: int = 6) -> dict:
    """Search the web and return titles, urls and snippets."""
    if not query.strip():
        return {"ok": False, "error": "empty query"}
    try:
        from bs4 import BeautifulSoup
        r = httpx.post(_DDG, data={"q": query}, timeout=20.0,
                       headers={"User-Agent": _UA}, follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "spoken": "I could not reach the search engine."}

    results = []
    for res in soup.select(".result")[:limit * 2]:
        a = res.select_one(".result__a")
        if not a:
            continue
        snip = res.select_one(".result__snippet")
        url = a.get("href", "")
        # DuckDuckGo wraps results in a redirect; unwrap to the real URL.
        if "uddg=" in url:
            from urllib.parse import parse_qs, unquote, urlparse
            q = parse_qs(urlparse(url).query).get("uddg")
            if q:
                url = unquote(q[0])
        results.append({
            "title": a.get_text(" ", strip=True)[:120],
            "url": url[:200],
            "snippet": (snip.get_text(" ", strip=True)[:240] if snip else ""),
        })
        if len(results) >= limit:
            break

    if not results:
        return {"ok": False, "query": query, "error": "no results",
                "spoken": f"I found nothing for {query}."}
    return {"ok": True, "query": query, "count": len(results), "results": results,
            "spoken": f"Found {len(results)} results."}


@pinned
def read_url(url: str, max_chars: int = 5000) -> dict:
    """Fetch a page and return its readable text — no browser required.

    Strips script/style/nav so what comes back is the article, not the chrome.
    """
    try:
        from bs4 import BeautifulSoup
        r = httpx.get(url, timeout=25.0, headers={"User-Agent": _UA},
                      follow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "spoken": "I could not read that page."}

    for tag in soup(["script", "style", "nav", "header", "footer", "aside",
                     "noscript", "form"]):
        tag.decompose()
    main = soup.select_one("article, main, [role=main]") or soup.body or soup
    text = "\n".join(l.strip() for l in main.get_text("\n").splitlines() if l.strip())
    title = (soup.title.get_text(strip=True) if soup.title else "")[:120]
    return {"ok": True, "url": url[:200], "title": title,
            "text": text[:max_chars], "truncated": len(text) > max_chars}


@pinned
def close_tabs(url_fragment: str = "", keep_one: bool = True) -> dict:
    """Close tabs whose URL contains a fragment. Returns how many really closed.

    Tabs were openable and not closeable, so a session that browsed five sites
    kept five live pages - each one holding memory, timers and sockets for the
    rest of the day. Closing is verified by re-reading the page list rather
    than trusting close() to have worked.

    The HUD's own page is never closed, and `keep_one` leaves a last tab alive
    because a Chrome with zero pages exits and takes the debugging port with
    it, which would end the session that asked for the tidy-up."""
    ensure()
    pages = _pages()
    frag = (url_fragment or "").lower()

    doomed = [p for p in pages
              if (not frag or frag in (p.url or "").lower())
              and not _is_hud(p.url or "")]
    if keep_one and len(doomed) >= len(pages) and doomed:
        doomed = doomed[1:]           # keep one alive so Chrome stays up

    closed, trouble = 0, []
    for p in doomed:
        try:
            p.close()
            closed += 1
        except Exception as e:
            # Reported, never swallowed: a tab that would not close is the
            # thing a leak investigation needs to know about.
            trouble.append(f"{(p.url or '')[:60]}: {type(e).__name__}")

    remaining = [(p.url or "") for p in _pages()]
    still = [u for u in remaining if frag and frag in u.lower()]
    out = {"ok": not still, "closed": closed, "remaining": len(remaining),
           "still_open": still[:5]}
    if trouble:
        out["errors"] = trouble
    return out


# ─────────────────────────────────────────────────────────────────────────────
# WHICH CHROME IS THIS, AND CAN I SEE IT?
#
# The YouTube Music failure was not really a step-limit failure. The owner
# said "in MY chrome youtube music is playing", and his Chrome runs on the
# DEFAULT profile with no debugging port, which Chrome 136+ refuses to open
# there on purpose. Jarvis attaches to its OWN profile on port 9222. So the
# tab genuinely was not visible, every attempt to find it failed, and the
# agent burned its twelve reasoning rounds before the counter blamed the task
# for being long.
#
# Guessing harder was never going to work. What was missing was the ability to
# SAY SO: to look at the real process table, know which browsers exist, know
# which of them can be driven, and report the difference with the remedy
# attached instead of searching a browser that was never going to have it.
# ─────────────────────────────────────────────────────────────────────────────

def chrome_instances() -> list[dict]:
    """Every Chrome running, and whether Jarvis can drive it.

    Reads the real process table. `reachable` means it was started with a
    debugging port, which is the only way CDP can attach - it cannot be turned
    on afterwards, so a running Chrome without it stays invisible until it is
    restarted."""
    import re

    import psutil
    out = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        if (p.info.get("name") or "").lower() != "chrome.exe":
            continue
        cl = " ".join(p.info.get("cmdline") or [])
        if "--type=" in cl:
            continue                      # renderer, gpu, utility children
        port = re.search(r"--remote-debugging-port=(\d+)", cl)
        udd = re.search(r'--user-data-dir="?([^"]+?)"?(?: --|$)', cl)
        profile = udd.group(1) if udd else "DEFAULT"
        out.append({
            "pid": p.info["pid"],
            "port": int(port.group(1)) if port else None,
            "profile": profile,
            "is_jarvis": PROFILE_DIR.lower() in profile.lower(),
            "reachable": bool(port),
        })
    return out


def browsing_context() -> dict:
    """What Jarvis can and cannot see right now, in terms a person can act on.

    This is what a task should consult BEFORE hunting for a tab, so an
    unreachable objective becomes a clear answer rather than a search that
    cannot succeed."""
    instances = chrome_instances()
    mine = [c for c in instances if c["is_jarvis"]]
    theirs = [c for c in instances if not c["is_jarvis"]]
    unreachable = [c for c in theirs if not c["reachable"]]

    visible = []
    if debug_port_open():
        try:
            visible = [t.get("url", "") for t in tabs().get("tabs", [])]
        except Exception as e:
            visible = [f"(could not list tabs: {type(e).__name__})"]

    return {
        "jarvis_chrome": bool(mine),
        "jarvis_tabs": visible,
        "other_chromes": len(theirs),
        "unreachable_chromes": len(unreachable),
        "can_see_owner_browser": not unreachable,
        "remedy": (
            "Your own Chrome is running without a debugging port, so I cannot "
            "read its tabs - Chrome refuses to open that port on the default "
            "profile, and it cannot be switched on while it is running. Either "
            "let me open the page in my own Chrome, or close yours and start "
            "it with --remote-debugging-port=9223 and I will attach to that."
        ) if unreachable else "",
    }


def attach_to(port: int) -> dict:
    """Drive a DIFFERENT Chrome - the owner's, if he started it with a port.

    Kept separate from ensure() so attaching to the owner's browser is always
    a deliberate act. It is his session, with his logins and his tabs."""
    global _browser, _pw, DEBUG_PORT
    import httpx as _httpx
    try:
        _httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=3.0)
    except Exception as e:
        return {"ok": False,
                "error": f"nothing is listening on port {port} "
                         f"({type(e).__name__}), so there is no browser to attach to"}
    shutdown()
    DEBUG_PORT = port
    try:
        _connect(fresh=True)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "port": port,
            "tabs": [t.get("url", "")[:90] for t in tabs().get("tabs", [])]}


# ─────────────────────────────────────────────────────────────────────────────
# READING A PAGE THAT BUILDS ITSELF
#
# A dynamically rendered app has no useful HTML at load time, so "fetch the
# URL and parse it" returns a shell. The escalation below is general and
# ordered by cost:
#
#   1. a STANDARD the page already implements  (MediaSession, <audio>/<video>)
#   2. the ACCESSIBILITY contract              (role, aria-label, title)
#   3. rendered text                           (innerText of the live DOM)
#
# Nothing here knows what YouTube Music is. MediaSession is a W3C API that
# Spotify, SoundCloud, YouTube and YouTube Music all populate because browsers
# and operating systems read it for their own media controls - so asking for
# it works on any of them and will keep working when their markup changes,
# which a layout-specific parser would not.
# ─────────────────────────────────────────────────────────────────────────────

_MEDIA_JS = r"""() => {
    const out = {source: null, playing: false, title: null, artist: null,
                 album: null, position: null, duration: null, queue: []};

    // 1. The standard. Whatever the site is, if it plays media for a person
    //    it almost certainly fills this in for the OS media controls.
    const ms = navigator.mediaSession;
    if (ms && ms.metadata) {
        out.source = 'mediaSession';
        out.title = ms.metadata.title || null;
        out.artist = ms.metadata.artist || null;
        out.album = ms.metadata.album || null;
        out.playing = ms.playbackState === 'playing';
    }

    // 2. The elements themselves - authoritative about whether sound is
    //    actually coming out, which metadata alone does not tell you.
    const els = [...document.querySelectorAll('audio, video')];
    const live = els.find(e => !e.paused && !e.ended && e.readyState > 2);
    if (live) {
        out.source = out.source || 'element';
        out.playing = true;
        out.position = Math.round(live.currentTime);
        out.duration = Number.isFinite(live.duration) ? Math.round(live.duration) : null;
    } else if (els.length && !out.source) {
        out.source = 'element';
        out.playing = false;
    }
    // 3. WHAT PLAYS NEXT. Three strategies, none naming a site.
    //
    //    (a) A container that CALLS ITSELF a queue or up-next, found through
    //        the accessibility contract. music.youtube.com has one.
    //
    //    (b) Failing that, links to another page OF THE SAME SHAPE as this
    //        one, in document order. On a watch page those are the upcoming
    //        videos, and it needed no knowledge of the site to find them -
    //        youtube.com has no role="list" and no "up next" heading at all,
    //        so (a) alone returned nothing there while the queue was plainly
    //        on screen as fifty-seven links.
    const seen = new Set();
    const rowSel = '[role="listitem"], li, ytmusic-player-queue-item, tr';
    const lists = [];
    for (const el of document.querySelectorAll(
            '[aria-label*="queue" i], [aria-label*="up next" i], ' +
            '[role="list"][aria-label], ytmusic-player-queue, #queue, .queue'))
        lists.push([el, 'labelled-queue']);

    //    (a2) A panel named by a VISIBLE HEADING rather than an attribute.
    //         Reading a heading that says "Queue" is not knowing the site - a
    //         panel with that title is titled that everywhere. It matters
    //         because a queue the user actually built sits under exactly such
    //         a heading on a video page, and without this it fell through to
    //         (b) and came back indistinguishable from mere suggestions.
    const named = /^\s*(?:queue|up next|next up|playing next)\b/i;
    for (const h of document.querySelectorAll('h1,h2,h3,h4,[role="heading"]')) {
        const t = (h.textContent || '').replace(/\s+/g, ' ').trim();
        if (!t || t.length > 40 || !named.test(t)) continue;
        let n = h.parentElement;
        for (let up = 0; up < 5 && n; up++, n = n.parentElement) {
            if (n.querySelectorAll(rowSel).length >= 2) {
                lists.push([n, 'named-panel']);
                break;
            }
        }
    }

    for (const pair of lists) {
        const list = pair[0], src = pair[1];
        for (const row of list.querySelectorAll(rowSel)) {
            const text = (row.innerText || '').trim().split('\n')
                .filter(Boolean).slice(0, 2).join(' - ');
            if (text && text.length < 200 && !seen.has(text)) {
                seen.add(text);
                out.queue.push({text: text,
                                current: row.matches('[selected], [aria-selected="true"], .selected')});
            }
            if (out.queue.length >= 25) break;
        }
        if (out.queue.length) { out.queue_source = src; break; }
    }

    if (!out.queue.length) {
        // Link-based strategies. A control ("Next (SHIFT+n)") and the link
        // back to this very item are skipped, or the first two rows of every
        // answer would be a keyboard hint and the track already playing.
        const here = location.pathname;
        const control = /\((?:shift|ctrl|alt)\b|^\s*(?:next|previous|prev|play|pause|skip)\s*$/i;
        const now = (out.title || '').trim().toLowerCase();

        //    (a3) THE COLLECTION THIS PAGE SAYS IT IS PLAYING. When the
        //         address carries a collection id, the links carrying the
        //         SAME id are that collection in order - the queue, asserted
        //         by the page's own links rather than by any word on screen.
        //         This is the difference between "the rest of your playlist"
        //         and "things you might also like", and the two must not be
        //         reported in the same sentence.
        let collectionKey = '', collectionVal = '';
        const params = new URLSearchParams(location.search);
        for (const k of ['list', 'playlist', 'album', 'set']) {
            const v = params.get(k);
            if (v) { collectionKey = k; collectionVal = v; break; }
        }

        const gather = (needCollection) => {
            const rows = [], urls = new Set();
            for (const a of document.querySelectorAll('a[href]')) {
                let u; try { u = new URL(a.href, location.href); } catch (e) { continue; }
                if (u.pathname !== here) continue;
                if (u.href === location.href) continue;
                if (urls.has(u.href)) continue;
                if (needCollection &&
                    new URLSearchParams(u.search).get(collectionKey) !== collectionVal) continue;
                const title = (a.getAttribute('title') || a.getAttribute('aria-label')
                            || a.textContent || '').replace(/\s+/g, ' ').trim();
                if (!title || title.length < 3 || title.length > 180) continue;
                if (control.test(title)) continue;
                if (now && title.toLowerCase() === now) continue;
                urls.add(u.href);
                rows.push({text: title.slice(0, 120), current: false,
                           href: u.href.slice(0, 80)});
                if (rows.length >= 25) break;
            }
            return rows;
        };

        if (collectionKey) {
            const rows = gather(true);
            if (rows.length) { out.queue = rows; out.queue_source = 'same-collection-links'; }
        }
        if (!out.queue.length) {
            const rows = gather(false);
            if (rows.length) { out.queue = rows; out.queue_source = 'same-shape-links'; }
        }
    }
    return out;
}"""


@pinned
def media_state(url_fragment: str = "") -> dict:
    """What is playing on a page, and what is queued after it.

    Works by standards, not by site. Returns `found: False` with a reason
    rather than inventing a track, because a confident wrong answer about what
    is playing is worse than admitting the page did not say.

    WITH NO FRAGMENT IT FINDS THE TAB THAT IS ACTUALLY PLAYING, rather than
    whichever page happens to be frontmost. "My chrome has a tab playing a
    song" names the tab by what it is doing, and _active_page() answers a
    different question - so with two media tabs open it read the idle one and
    reported its queue as the answer. `considered` lists every tab weighed and
    why this one won, so a wrong pick is visible instead of silent.

    WITH A FRAGMENT IT READS THAT SITE AND NO OTHER. Name the host when the
    request names a site: media_state("youtube.com") is not the same question
    as media_state("music.youtube.com"), and answering one with the other is
    the failure this signature exists to prevent."""
    ensure()
    considered: list[dict] = []
    if url_fragment:
        page = page_for(url_fragment)
        if page is None:
            return {"ok": False, "found": False,
                    "error": f"no tab matching {url_fragment!r} is open",
                    "open_tabs": [t["url"] for t in (tabs().get("tabs") or [])][:12]}
    else:
        best, best_rank = None, -1
        for cand in _pages()[:12]:
            try:
                url = cand.url or ""
                if _is_hud(url) or url.startswith(("about:", "chrome://")):
                    continue
                probe_data = cand.evaluate(_MEDIA_JS)
            except Exception:
                continue
            playing = bool(probe_data.get("playing"))
            queued = bool(probe_data.get("queue"))
            named = bool(probe_data.get("title"))
            # playing with a queue beats playing, which beats merely knowing a
            # title, which beats a page that is only capable of media
            r = (4 if playing and queued else 3 if playing
                 else 2 if named else 1 if probe_data.get("source") else 0)
            considered.append({"url": url[:100], "playing": playing,
                               "title": (probe_data.get("title") or "")[:60],
                               "rank": r})
            if r > best_rank:
                best, best_rank = cand, r
        if best is None:
            return {"ok": False, "found": False, "error": "no page is open",
                    "considered": considered}
        if best_rank == 0:
            return {"ok": True, "found": False, "url": (best.url or "")[:120],
                    "queue": [], "considered": considered,
                    "note": "no open tab is playing anything"}
        page = best
    try:
        data = page.evaluate(_MEDIA_JS)
    except Exception as e:
        return {"ok": False, "found": False,
                "error": f"could not read the page: {type(e).__name__}: {e}"}

    found = bool(data.get("title") or data.get("source"))

    # Say WHERE the queue came from in words, not just as a strategy name.
    # The rows differ in kind: a panel the user filled is a queue, whereas
    # links the page merely offers are suggestions. Reported without that
    # distinction, a suggestion gets relayed as "next in your queue" - true
    # in shape, wrong in fact, and unfalsifiable by the person listening.
    PROVENANCE = {
        "labelled-queue": "read from the player's own queue list",
        "named-panel": "read from a panel on the page titled as the queue",
        "same-collection-links": "the rest of the playlist this page is "
                                 "playing, in order",
        "same-shape-links": "the page exposes no queue list, so these are what "
                            "it offers to play next - suggestions or autoplay, "
                            "not a queue anyone built",
    }
    src = data.get("queue_source") or ""
    return {"ok": True, "found": found, "url": (page.url or "")[:120],
            **data,
            "queue_note": PROVENANCE.get(src, ""),
            **({"considered": considered} if considered else {}),
            "note": ("" if found else
                     "this page exposes no media state at all - it may not be "
                     "a player, or nothing has started")}


@pinned
def read_dynamic(url_fragment: str = "", selector: str = "",
                 settle_ms: int = 2500, max_chars: int = 6000) -> dict:
    """Read a page AFTER it has built itself, escalating until something works.

    The escalation is reported in `via`, so a thin answer is recognisable as a
    thin answer rather than looking like the whole page."""
    ensure()
    page = page_for(url_fragment) if url_fragment else _active_page()
    if page is None:
        return {"ok": False, "error": f"no tab matching {url_fragment!r}"}

    # Let it finish rendering, but only until it is quiet - not a flat wait.
    try:
        page.wait_for_load_state("networkidle", timeout=settle_ms)
    except Exception:
        pass

    attempts = []
    if selector:
        try:
            page.wait_for_selector(selector, timeout=settle_ms, state="attached")
            text = page.locator(selector).first.inner_text(timeout=3000)
            if (text or "").strip():
                return {"ok": True, "via": "selector", "selector": selector,
                        "text": text[:max_chars]}
            attempts.append(f"selector {selector!r}: present but empty")
        except Exception as e:
            attempts.append(f"selector {selector!r}: {type(e).__name__}")

    try:
        aria = page.evaluate(
            """() => [...document.querySelectorAll('[aria-label]')]
                 .map(e => e.getAttribute('aria-label'))
                 .filter(t => t && t.length > 2).slice(0, 120).join(' | ')""")
        if (aria or "").strip():
            attempts.append("aria: found")
        else:
            attempts.append("aria: nothing labelled")
    except Exception as e:
        aria = ""
        attempts.append(f"aria: {type(e).__name__}")

    try:
        body = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception as e:
        return {"ok": False, "error": f"the page would not render text: {e}",
                "attempts": attempts}

    if (body or "").strip():
        return {"ok": True, "via": "rendered-text", "text": body[:max_chars],
                "aria": (aria or "")[:1500], "attempts": attempts}
    if (aria or "").strip():
        return {"ok": True, "via": "aria", "text": aria[:max_chars],
                "attempts": attempts}
    return {"ok": False, "via": "none",
            "error": "the page rendered no text and exposed no labels",
            "attempts": attempts}


def playwright_driver():
    """The ONE sync Playwright driver for this process.

    Playwright's sync API allows exactly one driver per thread, and everything
    browser-ish in Jarvis runs on the single pinned "cdp" thread. browser.py
    and lib/whatsapp.py each used to call sync_playwright().start() of their
    own, which worked for whichever ran first and failed for the other with

        It looks like you are using Playwright Sync API inside the asyncio loop

    - a confusing message for what is really "there is already a driver here".
    Nothing caught it until a check exercised WhatsApp and Chrome in the same
    process, because until then only one of them ever ran.

    Both now borrow this. It is created on first use and never twice."""
    global _pw
    if _pw is None:
        from playwright.sync_api import sync_playwright
        _pw = sync_playwright().start()
    return _pw
