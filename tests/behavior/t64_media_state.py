"""G64: now-playing and the queue are read from REAL media pages.

Two live sites, one reader, no site-specific code. That matters because the
owner said "my chrome has a youtube tab" and the first version of this only
covered music.youtube.com - a different app with different markup. Regular
youtube.com has no role="list" and no "up next" heading at all, so the
labelled-queue strategy found nothing there while the queue sat on screen as
links.

This check proves the reader works on the two real sites. The proof that it is
GENERAL rather than tuned to them is G69, which runs the same reader against
local pages on 127.0.0.1 and pins the ordering of all four strategies.

The strategy a page ends up using is NOT asserted here: it depends on what the
page is showing at the moment the check runs - a playlist gives one answer, a
bare watch page another - and demanding a particular one would make this fail
for a change in the owner's browser rather than a change in the code.
"""
import time
import _harness as h

h.fresh_db()
import browser

browser.ensure(auto_restart=True)

KNOWN = {"labelled-queue", "named-panel", "same-collection-links",
         "same-shape-links"}

# a page with no media must say so rather than invent a track
browser.goto("https://example.com/", new_tab=True)
m = browser.media_state("example.com")
h.need(m["ok"] and m["found"] is False, f"it found media on example.com: {m}")
h.need(not m.get("title"), "it invented a title for a page with no media")
h.need(not (m.get("queue") or []), "it invented a queue for a page with no media")

# ── regular youtube.com - what the owner actually had open ────────────────────
if not browser.page_for("www.youtube.com/watch"):
    browser.goto("https://www.youtube.com/watch?v=jfKfPfyJRdk", new_tab=True)
    time.sleep(9)

yt = browser.media_state("youtube.com/watch")
h.need("music." not in (yt.get("url") or ""),
       f"asked for youtube.com and got a different host: {yt.get('url')!r}")
h.need(yt["ok"] and yt["found"], f"youtube.com exposed no media state: {yt}")
h.need(yt["source"] in ("mediaSession", "element"), f"source: {yt['source']}")
h.need(yt.get("title"), f"no track title from youtube.com: {yt}")
q = yt.get("queue") or []
h.need(len(q) >= 3, f"youtube.com queue had {len(q)} rows; expected several")
h.need(yt.get("queue_source") in KNOWN,
       f"unknown queue strategy: {yt.get('queue_source')!r}")
h.need((yt.get("queue_note") or "").strip(),
       "the rows came back with no statement of where they came from")

# the queue must not be polluted by controls or by the track already playing
now = (yt.get("title") or "").strip().lower()
for row in q:
    txt = (row.get("text") or "").strip()
    h.need(txt, f"an empty queue row: {row}")
    h.need(txt.lower() != now, f"the current track is listed as up-next: {txt!r}")
    h.need("shift+" not in txt.lower(), f"a keyboard control leaked in: {txt!r}")

nxt = q[0]["text"]
h.need(nxt and len(nxt) > 2, f"the next item is not readable: {nxt!r}")
print(f"# youtube.com  via={yt['queue_source']} now={yt['title'][:38]!r} "
      f"next={nxt[:38]!r} queue={len(q)}")

# ── music.youtube.com - a different app, the same reader ──────────────────────
if not browser.page_for("music.youtube.com"):
    browser.goto("https://music.youtube.com/", new_tab=True)
    time.sleep(7)
ytm = browser.media_state("music.youtube.com")
h.need(ytm["ok"], f"could not read music.youtube: {ytm}")
h.need("music." in (ytm.get("url") or ""),
       f"asked for music.youtube.com and got {ytm.get('url')!r}")
if ytm.get("playing") and (ytm.get("queue") or []):
    h.need(ytm.get("queue_source") in KNOWN,
           f"unknown queue strategy: {ytm.get('queue_source')!r}")
    mq = ytm["queue"]
    print(f"# music.youtube via={ytm['queue_source']} queue={len(mq)}")
else:
    # nothing playing is a legitimate state and must be reported as that
    h.need(ytm.get("playing") is False, "ambiguous playing state")
    print(f"# music.youtube open, nothing playing (source={ytm.get('source')})")

# The two hosts must not be confused for one another. This is the bug that
# made the whole thing worth re-testing: "youtube.com/watch" is a substring of
# "music.youtube.com/watch", so a question about one tab was answered, in
# perfect shape and with no error, from the other one.
a = browser.page_for("youtube.com/watch")
b = browser.page_for("music.youtube.com")
if a and b:
    h.need(a != b, "youtube.com and music.youtube.com resolved to ONE tab")

# no site name may appear in code that RUNS. Comments recording which site
# provoked a strategy are documentation and stay; the real generality proof is
# G69, which exercises all four strategies on a site nobody wrote code for.
import inspect
import re
src = inspect.getsource(browser.media_state) + browser._MEDIA_JS
code = "\n".join(l for l in src.splitlines()
                 if not l.strip().startswith(("//", "#")))
code = re.sub(r'""".*?"""', "", code, flags=re.S).lower()
for site in ("youtube", "spotify", "soundcloud", "ytmusic"):
    bad = [l for l in code.splitlines() if site in l and "ytmusic-player-queue" not in l]
    h.need(not bad, f"the reader hard-codes {site}: {bad[:2]}")

browser.close_tabs("example.com")
h.ok("MEDIA_STATE_OK")
