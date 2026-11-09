"""
playbooks.py — curated knowledge of HOW popular sites and apps work, so Jarvis
drives them in a few correct steps instead of re-discovering them with expensive
screenshots every time.

This is the "training" the owner asked for - NOT a model fine-tune (slow,
unauditable, and it would not touch the token bill), but an auditable,
instantly-updatable knowledge base. When a task touches a known site, the
matching playbook is handed to the orchestrator as context, so it follows KNOWN
correct steps.

The guidance is written to keep COST and TIME down, because they are the same
thing here: every extra browser tab and every screenshot is another model/vision
call. So the rules in each playbook are: ONE tab, read the page's ACCESSIBILITY
structure (read_window / set_control / click_control) instead of taking
screenshots, and a clear "it is done" signal so it does not poll blindly.

Each playbook is DATA (no code), matched by keyword/host. Dropping a new entry in
PLAYBOOKS teaches Jarvis a new site - that is the whole install. There is also a
GENERIC entry for any site not listed, so an unknown site is still driven the
cheap, correct way rather than by flailing with screenshots.
"""
from __future__ import annotations

# Each: name, triggers (lowercase substrings matched against the request), and
# the steps block injected into the prompt. Keep steps concrete and short.
PLAYBOOKS: list[dict] = [
    {
        "name": "ChatGPT",
        "triggers": ["chatgpt", "chat gpt", "chat.openai", "openai chat",
                     "in gpt", "ask gpt", "on gpt"],
        "steps": (
            "KNOWN SITE - ChatGPT (chatgpt.com), in his signed-in Chrome. When "
            "he asks to do something IN ChatGPT, use HIS ChatGPT - do it, do not "
            "make the content yourself. Drive it CHEAPLY and FAST:\n"
            "1. Open it ONCE: open_chrome_profile('main', 'https://chatgpt.com'). "
            "ONE TAB. If you already opened ChatGPT this task, DO NOT open it "
            "again - reuse the open tab. Opening 5-8 tabs is the exact bug to "
            "avoid.\n"
            "2. Give the WHOLE prompt in ONE message - never in pieces. To type "
            "it: desktop specialist read_window on the Chrome window to find the "
            "message box (its accessibility name is like 'Message ChatGPT' / "
            "'Send a message'), set_control to put the FULL prompt there, then "
            "cursor_key('enter') or click the Send control. Do NOT screenshot to "
            "find the box - read_window is exact and free.\n"
            "3. Wait for it to finish: while generating, ChatGPT shows a 'Stop' "
            "button; when done it becomes the send/voice control. Re-read the "
            "window every ~3s until the Stop button is gone (a few polls), THEN "
            "read the final answer with read_text('ChatGPT') - that returns the "
            "answer TEXT via the accessibility layer, NEVER a screenshot.\n"
            "4. Do what he asked WITH that answer (e.g. build the deck from it "
            "with make_pptx). Never take a screenshot for this flow and never "
            "open a second tab - that is what cost 100 rupees and minutes."),
    },
    {
        "name": "WhatsApp",
        "triggers": ["whatsapp", "whats app", "wa.me", "message on whatsapp"],
        "steps": (
            "KNOWN APP - WhatsApp is the COMMS specialist's job, ONE call, never "
            "the cursor or screenshots. whatsapp_send(contact, text) opens "
            "WhatsApp, finds the contact over its own protocol, sends, and reads "
            "the message back as proof. whatsapp_read(contact) reads their latest "
            "incoming before you reply. If the name matches more than one chat, "
            "report the options; do not guess. Never drive WhatsApp by "
            "screenshots / cursor / key-presses."),
    },
    {
        "name": "Gmail",
        "triggers": ["gmail", "email", "e-mail", "mail to", "send a mail",
                     "compose"],
        "steps": (
            "KNOWN APP - Email is the COMMS specialist, DRAFT-FIRST always: "
            "email_draft(to, subject, body), SHOW him the draft, and STOP - "
            "email_send(draft_id) only after he explicitly says send. Never "
            "auto-send to a client. To read, read_email. Do NOT drive the Gmail "
            "website with the cursor/screenshots; use the email tools."),
    },
    {
        "name": "YouTube",
        "triggers": ["youtube", "you tube", "yt ", "play a song", "play music",
                     "play the video", "play a video"],
        "steps": (
            "KNOWN SITE - YouTube. To play something, the browser specialist "
            "opens/searches it by the page's structure (role/text), not pixels; "
            "media keys (media tool) do play/pause/next. To SKIP AN AD: wait for "
            "the 'Skip' button to appear, then click it ONCE - use "
            "locate_on_screen + cursor only if there is no structured handle, and "
            "LOOK once to confirm the video (not an ad) is playing before saying "
            "it is. Read what is on screen from the page structure, not repeated "
            "screenshots."),
    },
]

# For ANY site not listed above - still drive it the cheap, correct way.
GENERIC = (
    "DRIVING A WEBSITE OR APP CHEAPLY (this is how you keep it fast and the bill "
    "low):\n"
    "- Open it in ONE tab. Never open the same page again - reuse the open tab.\n"
    "- READ the page through its structure, not screenshots: the browser "
    "specialist (role/text) for web pages, read_window (controls) and read_text "
    "(the readable text) via the accessibility tree for app windows. Screenshots "
    "(see_screen/locate) cost a vision call each - "
    "use them ONLY when there is no structured handle (a game, a canvas, an "
    "image, an ad overlay), and then once, not repeatedly.\n"
    "- Type a whole input at once (set_control / one type), submit, then wait for "
    "a clear DONE signal before reading the result.\n"
    "- Keep to the fewest steps that finish the job - every extra step is time "
    "and money.")


def match(text: str) -> list[dict]:
    """The playbooks relevant to this request, most specific first. Pure and
    free - substring match, no model call."""
    t = (text or "").lower()
    hits = []
    for pb in PLAYBOOKS:
        if any(trig in t for trig in pb["triggers"]):
            hits.append(pb)
    return hits


def block(text: str) -> str:
    """Prompt-ready knowledge for this request: the matching site playbook(s),
    or '' when none match (then the orchestrator's own rules + GENERIC apply).

    Only injected when a known site is in play, so it costs nothing on an
    ordinary turn and the system prompt stays byte-identical for the cache."""
    hits = match(text)
    if not hits:
        return ""
    parts = [pb["steps"] for pb in hits]
    return "\n\n".join(parts) + "\n"
