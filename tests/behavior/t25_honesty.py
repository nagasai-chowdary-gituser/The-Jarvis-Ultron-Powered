"""G25: an unearned completion claim never reaches the owner intact."""
import _harness as h

h.fresh_db()
import brain

# "monitoring" with no job scheduled this turn
out = brain.vet("I'll keep monitoring your inbox.", {"job_ids": []})
h.need("not monitoring" in out.lower(), f"the false claim survived: {out!r}")

# with a real job id it is allowed to stand
out = brain.vet("I'll keep monitoring your inbox.", {"job_ids": [7]})
h.need("monitoring" in out and "not monitoring" not in out.lower(),
       f"a true claim was mangled: {out!r}")
h.need("#7" in out or "job" in out.lower(), "the job id was not surfaced")

# "done" with nothing behind it is contradicted
out = brain.vet("Done - I sent it.", {"results": [], "job_ids": []})
h.need("did NOT happen" in out, f"an invented completion survived: {out!r}")

# backed by a real result it stands
out = brain.vet("Done - I sent it.", {"results": [{"ok": True}], "job_ids": []})
h.need("did NOT happen" not in out, f"a true completion was contradicted: {out!r}")

# a failure reason is reported in human words, not as library vomit
junk = ("Traceback (most recent call last):\n  File \"x.py\", line 5\n"
        "playwright._impl._errors.TimeoutError: Locator.click: Timeout 6000ms\n"
        "Call log:\n  - locator resolved to <input class=\"xdj266r x14z9mp "
        "xat24cr x1lziwak xexx8yu x18d9i69\">")
out = brain.vet("Sent it.", {"results": [], "job_ids": [],
                             "failures": [{"error": junk}]})
h.need("did NOT happen" in out, "the failure was not contradicted")
h.need("xdj266r" not in out and "x14z9mp" not in out,
       f"raw CSS class names reached the owner: {out!r}")
h.need("Traceback" not in out, f"a traceback reached the owner: {out!r}")
h.need(len(out) < 300, f"the message is a wall of text: {len(out)} chars")

# every reply carries a source tag
for reply in ("The disk is fine.", "Chrome is open."):
    tagged = brain.vet(reply, {"results": [{"ok": True}], "job_ids": []})
    h.need(tagged.strip().startswith("["), f"no source tag on: {tagged!r}")

h.ok("HONESTY_OK")
