"""G65: playback is driven and the change VERIFIED in the real page."""
import time
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)
if not browser.page_for("music.youtube"):
    browser.goto("https://music.youtube.com/search?q=lofi", new_tab=True)
    time.sleep(6)

def playing():
    return bool(browser.media_state("music.youtube").get("playing"))

def real_click(sel):
    """A CDP click, which Chrome accepts as a user gesture. A JS .click()
    is NOT accepted by the autoplay policy - found the hard way."""
    def go(p):
        loc = p.locator(sel).first
        if not loc.count():
            return False
        loc.click(timeout=8000, force=True)
        return True
    try:
        return browser.on_page(go, "music.youtube")
    except Exception:
        return False

# Get something playing, escalating. The order matters: if a player already
# exists and is merely paused, RESUMING it is the cheap correct move, and
# clicking a search result instead would navigate away from the thing that was
# already loaded. That mistake cost this check a run.
def has_player():
    return bool(browser.on_page(
        lambda p: p.evaluate("() => document.querySelectorAll('audio,video').length > 0"),
        "music.youtube"))

if not playing() and has_player():
    # the player bar's own play control, clicked for real
    for sel in ('ytmusic-player-bar [aria-label*="Play" i]',
                '#play-pause-button',
                'tp-yt-paper-icon-button#play-pause-button'):
        if real_click(sel):
            time.sleep(2.5)
        if playing():
            break

if not playing():
    # a page that has already had a gesture accepts programmatic play()
    browser.on_page(lambda p: p.evaluate(
        """() => { const v=[...document.querySelectorAll('audio,video')][0];
                   if (v) { v.play(); return true; } return false; }"""),
        "music.youtube")
    time.sleep(2.5)

if not playing():
    # nothing loaded at all - start something from a search
    browser.goto("https://music.youtube.com/search?q=lofi", new_tab=False)
    time.sleep(6)
    for sel in ("ytmusic-play-button-renderer", '[aria-label*="Play" i]'):
        if real_click(sel):
            for _ in range(6):
                time.sleep(2)
                if playing():
                    break
        if playing():
            break

if not playing():
    # Honest outcome: the page would not start. Say so rather than pass a
    # control test that never controlled anything.
    h.fail("could not start playback, so pause/resume cannot be verified")

h.need(playing(), "playback did not start")
before = browser.media_state("music.youtube")
h.need(before.get("title"), "playing with no title")

# PAUSE, and verify from the page rather than from having clicked
paused = browser.on_page(
    lambda p: p.evaluate("""() => { const v=[...document.querySelectorAll('audio,video')]
        .find(e=>!e.paused); if(v){v.pause(); return true;} return false; }"""),
    "music.youtube")
h.need(paused, "nothing was there to pause")
time.sleep(1.5)
h.need(not playing(), "it reported pause but the page is still playing")

# RESUME, verified the same way
browser.on_page(
    lambda p: p.evaluate("""() => { const v=[...document.querySelectorAll('audio,video')]
        .find(e=>e.paused); if(v){v.play(); return true;} return false; }"""),
    "music.youtube")
time.sleep(2.5)
h.need(playing(), "it reported resume but the page is not playing")

after = browser.media_state("music.youtube")
h.need(after.get("title"), "no track after resuming")
print(f"# paused and resumed, verified from the page: {after['title'][:45]!r}")

h.ok("MEDIA_CONTROL_OK")
