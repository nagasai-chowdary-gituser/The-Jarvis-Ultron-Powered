"""G69 - what-plays-next is read by strategy, not by site.

THE POINT OF THIS CHECK IS THAT IT NEVER TOUCHES YOUTUBE. The reader was built
while looking at one music site, and a reader verified only against the site it
was built for is indistinguishable from a parser for that site. So the four
strategies are exercised against local pages served over HTTP: the DOM, the
browser and the JS are real, and the site is not one anybody wrote code for.

It also pins the ORDER of the strategies, which is where the meaning lives. A
list the page labels as its queue outranks a panel merely titled that, which
outranks the rest of the playlist in the address, which outranks links the page
happens to offer. Collapsing those would let a suggestion be relayed as "next
in your queue" - correctly shaped, factually wrong, and unarguable by the
person listening to it.
"""
import functools
import http.server
import socket
import threading
from pathlib import Path

from _harness import need, ok, fail

import browser

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _serve() -> tuple[str, http.server.ThreadingHTTPServer]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(FIXTURES))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", httpd


def main() -> None:
    base, httpd = _serve()
    host = base.split("//")[1]                      # 127.0.0.1:PORT
    opened = []
    try:
        def read(page: str, query: str = "") -> dict:
            url = f"{base}/{page}{query}"
            g = browser.goto(url, new_tab=True)
            need(g.get("ok"), f"could not open {url}: {g}")
            opened.append(url)
            m = browser.media_state(f"{host}/{page}")
            need(m.get("ok"), f"media_state failed on {page}: {m}")
            need((m.get("url") or "").startswith(base),
                 f"read the wrong tab for {page}: {m.get('url')}")
            return m

        def texts(m: dict) -> list[str]:
            return [r.get("text", "") for r in (m.get("queue") or [])]

        # (a) the page labels its own queue through the accessibility contract
        m = read("labelled.html")
        need(m["queue_source"] == "labelled-queue",
             f"aria-labelled queue read as {m['queue_source']!r}")
        rows = texts(m)
        need(rows[:3] == ["Aria Row One", "Aria Row Two", "Aria Row Three"],
             f"labelled rows wrong: {rows}")
        need(not any("Shape Link" in t for t in rows),
             f"a labelled queue was polluted with page links: {rows}")
        need(any(r.get("current") for r in m["queue"]),
             "aria-selected row was not marked as the current one")
        need("own queue" in (m.get("queue_note") or ""),
             f"provenance not stated: {m.get('queue_note')!r}")

        # (a2) no aria-label anywhere; only a visible heading says "Up next"
        m = read("named.html")
        need(m["queue_source"] == "named-panel",
             f"heading-named panel read as {m['queue_source']!r}")
        rows = texts(m)
        need(rows[:3] == ["Heading Row One", "Heading Row Two", "Heading Row Three"],
             f"named-panel rows wrong: {rows}")
        need(not any("Shape Link" in t for t in rows),
             f"named panel was polluted with page links: {rows}")

        # (a3) no queue container at all - the address names the collection
        m = read("collection.html", "?v=now&list=ABC")
        need(m["queue_source"] == "same-collection-links",
             f"playlist in the address read as {m['queue_source']!r}")
        rows = texts(m)
        need(rows == ["In Collection One", "In Collection Two", "In Collection Three"],
             f"collection rows wrong: {rows}")
        need(not any("Other Collection" in t for t in rows),
             "a DIFFERENT collection's links were mixed into the queue")
        need(not any("ABCD" in t for t in rows),
             "list=ABCD matched list=ABC - the id is compared as a substring")
        need(not any("No Collection" in t for t in rows),
             "links with no collection id were counted as part of the playlist")
        need("playlist" in (m.get("queue_note") or ""),
             f"provenance not stated: {m.get('queue_note')!r}")

        # (b) nothing named and no collection: links of the same shape, and the
        #     note must SAY they are only suggestions
        m = read("plain.html")
        need(m["queue_source"] == "same-shape-links",
             f"plain page read as {m['queue_source']!r}")
        rows = texts(m)
        need(len(rows) == 4 and all(t.startswith("Shape Link") for t in rows),
             f"same-shape rows wrong: {rows}")
        need(not any("Different Shape" in t for t in rows),
             "a link to a different page shape was counted as queued")
        need(not any("SHIFT" in t for t in rows),
             "a keyboard control was reported as a queued item")
        note = (m.get("queue_note") or "").lower()
        need("not a queue" in note and "suggestion" in note,
             f"suggestions were not declared as suggestions: {note!r}")

        # nothing at all: say so, never invent a track
        m = read("silent.html")
        need(m.get("found") is False,
             f"a page with no media reported found={m.get('found')}")
        need(not (m.get("queue") or []), f"invented a queue: {m.get('queue')}")
        need(not m.get("title"), f"invented a title: {m.get('title')!r}")

        # the host boundary: an exact host must outrank a subdomain of it
        need(browser.page_for("nosuchhost.invalid") is None,
             "page_for matched a host that is not open")

        ok("QUEUE-STRATEGIES-OK")
    except AssertionError as e:
        fail(str(e))
    finally:
        httpd.shutdown()
        for url in opened:
            try:
                browser.close_tabs(url.split("//")[1])
            except Exception:
                pass


main()
