"""G72: delegation is REVIEWED, not relayed. A separate judge grades the output.

The whole value of delegate.produce() is that it does not hand back whatever the
other tool said - it judges the result against what the Boss asked for, and loops
on the fixes. This proves the judge: it accepts good work, rejects shallow work
while keeping the fix instruction, and on its own failure falls toward ANOTHER
pass rather than waving unreviewed work through.
"""
import _harness as h

h.fresh_db()
import openai

from lib import delegate


class _Resp:
    def __init__(self, text):
        self.output_text = text
        self.usage = None


def _fake_openai(payload):
    """A stand-in OpenAI client whose responses.create returns a fixed string."""
    class _Responses:
        def create(self, **kw):
            return _Resp(payload)

    class _Client:
        def __init__(self, *a, **k):
            self.responses = _Responses()

    return _Client


# 1. Good output is accepted.
openai.OpenAI = _fake_openai('{"good": true, "fixes": "", "reason": "meets the brief"}')
v = delegate.judge("make an 8-slide deck", "exactly 8 slides on the topic",
                   "a complete, on-topic 8-slide deck")
h.need(v["good"] is True, f"the judge rejected good work: {v}")

# 2. Shallow output is rejected AND the fix instruction survives for the next loop.
openai.OpenAI = _fake_openai(
    'Here you go: {"good": false, "fixes": "add real sources and a summary slide", '
    '"reason": "too shallow"}')
v = delegate.judge("x", "y", "z")
h.need(v["good"] is False, "the judge passed shallow work")
h.need("sources" in v["fixes"], f"the fix instruction was lost: {v}")

# 3. A judge that cannot be parsed must fail toward another pass, never toward
#    silently approving unreviewed output.
openai.OpenAI = _fake_openai("the model said something that is not json at all")
v = delegate.judge("x", "y", "z")
h.need(v["good"] is False,
       "unparseable judging was treated as approval - unreviewed work would ship")

h.ok("DELEGATE_JUDGE_OK")
