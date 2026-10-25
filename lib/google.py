"""
lib/google.py — Jarvis's OWN eyes on the owner's Gmail and Calendar.

WHY ITS OWN ACCESS, not the browser. Reading the inbox and the schedule is what
turns the awareness loop from "watches WhatsApp" into a real PA. Jarvis cannot
ride the owner's main Chrome (Chrome blocks a debug port on the default profile)
and scraping Gmail in a browser is slow and brittle. The Google API is direct,
fast and read-only here - calendar.readonly + gmail.readonly, nothing that can
send or delete.

ONE-TIME SETUP (the owner does this once, ~15 min):
  1. console.cloud.google.com -> new project -> enable the Gmail API and the
     Google Calendar API.
  2. Create an OAuth client of type "Desktop app" -> download the JSON ->
     save it as  google_credentials.json  in the Jarvis folder.
  3. Run google.setup() (or say "connect google") - a browser opens, he approves
     once, and a token is saved to data/google_token.json. It refreshes itself
     after that; he never signs in again.

EVERYTHING DEGRADES. No credentials or no token -> every call returns a clear
"not set up" dict and the awareness loop simply has one fewer input. Nothing
here raises into a caller or blocks a turn.
"""
from __future__ import annotations

import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Read AND write - but writing is draft-and-confirm (the owner's rule): Jarvis
# composes a draft and shows it; it only sends/creates when he says so. There is
# deliberately NO delete scope anywhere - it can read, draft, send and create,
# never delete an email or an event.
#   calendar.events   read + create/edit events (not delete calendars)
#   gmail.readonly    read the inbox
#   gmail.compose     create drafts, and send them - cannot read other mail
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]
CREDS_FILE = ROOT / "google_credentials.json"      # the owner drops this in
TOKEN_FILE = ROOT / "data" / "google_token.json"   # written after consent


def _creds_path():
    """The OAuth client file, under either name. Google downloads it as
    client_secret_<id>.apps.googleusercontent.com.json, so rather than make the
    owner rename it, accept that name too."""
    if CREDS_FILE.exists():
        return CREDS_FILE
    for p in sorted(ROOT.glob("client_secret*.json")):
        return p
    return None


def is_setup() -> bool:
    """True once the owner has connected his Google account."""
    return TOKEN_FILE.exists()


def has_credentials() -> bool:
    return _creds_path() is not None


def _creds():
    """Valid credentials, refreshing if needed, or None if not connected."""
    if not TOKEN_FILE.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
    except Exception:
        return None
    return None


def setup() -> dict:
    """Run the one-time consent. Opens a browser; saves the token. Desk only."""
    creds_file = _creds_path()
    if creds_file is None:
        return {"ok": False,
                "error": "No OAuth client file in the Jarvis folder. Create an "
                         "OAuth 'Desktop app' client in Google Cloud (with the "
                         "Gmail and Calendar APIs enabled), download its JSON, "
                         "and save it in the Jarvis folder (either name is fine: "
                         "google_credentials.json or the client_secret_... one)."}
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(creds_file), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return {"ok": True, "note": "Google is connected. Jarvis can now read "
                                    "your calendar and inbox."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _service(name: str, version: str):
    creds = _creds()
    if not creds:
        return None
    try:
        from googleapiclient.discovery import build
        return build(name, version, credentials=creds, cache_discovery=False)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Calendar
# ─────────────────────────────────────────────────────────────────────────────

def calendar_upcoming(hours: int = 12, max_n: int = 10) -> dict:
    """Events in the next `hours`, soonest first."""
    svc = _service("calendar", "v3")
    if svc is None:
        return {"ok": False, "error": "Google is not set up - run setup() / "
                                      "say 'connect google'."}
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        end = now + datetime.timedelta(hours=hours)
        r = svc.events().list(
            calendarId="primary", timeMin=now.isoformat(),
            timeMax=end.isoformat(), singleEvents=True,
            orderBy="startTime", maxResults=max_n).execute()
        events = []
        for e in r.get("items", []):
            start = e.get("start", {})
            events.append({
                "start": start.get("dateTime") or start.get("date"),
                "summary": e.get("summary", "(no title)"),
                "location": e.get("location", ""),
                "link": e.get("hangoutLink", "")})
        return {"ok": True, "events": events}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Gmail
# ─────────────────────────────────────────────────────────────────────────────

def gmail_unread(max_n: int = 8) -> dict:
    """Unread messages in the primary inbox (not promos/social), newest first."""
    svc = _service("gmail", "v1")
    if svc is None:
        return {"ok": False, "error": "Google is not set up - run setup() / "
                                      "say 'connect google'."}
    try:
        q = "is:unread in:inbox -category:promotions -category:social"
        lst = svc.users().messages().list(
            userId="me", q=q, maxResults=max_n).execute()
        out = []
        for m in lst.get("messages", []):
            msg = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"]).execute()
            hdr = {h["name"]: h["value"]
                   for h in msg.get("payload", {}).get("headers", [])}
            out.append({"from": hdr.get("From", ""),
                        "subject": hdr.get("Subject", "(no subject)"),
                        "snippet": (msg.get("snippet", "") or "")[:140]})
        return {"ok": True, "unread": out, "count": len(out)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────────────────────
# Writing — DRAFT first, send/create on confirmation. Never delete.
# ─────────────────────────────────────────────────────────────────────────────

def create_draft(to: str, subject: str, body: str) -> dict:
    """Compose an email as a Gmail DRAFT - it is NOT sent. Returns the draft id
    and a preview to show the Boss; send_draft() sends it once he confirms."""
    svc = _service("gmail", "v1")
    if svc is None:
        return {"ok": False, "error": "Google is not set up."}
    try:
        import base64
        from email.mime.text import MIMEText
        m = MIMEText(body or "")
        m["to"] = to
        m["subject"] = subject or ""
        raw = base64.urlsafe_b64encode(m.as_bytes()).decode()
        d = svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}).execute()
        return {"ok": True, "draft_id": d["id"], "to": to, "subject": subject,
                "preview": (body or "")[:400],
                "note": "Draft created, NOT sent. Show it to the Boss; send only "
                        "when he confirms."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def send_draft(draft_id: str) -> dict:
    """Send a draft the Boss has confirmed. Returns the sent message id."""
    svc = _service("gmail", "v1")
    if svc is None:
        return {"ok": False, "error": "Google is not set up."}
    try:
        sent = svc.users().drafts().send(
            userId="me", body={"id": draft_id}).execute()
        return {"ok": True, "sent_id": sent.get("id"), "note": "Email sent."}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _normalize_dt(s: str) -> str:
    """Accept an ISO datetime; if it has no timezone, add the system's local
    offset so Google places it at the right wall-clock time."""
    import datetime
    s = (s or "").strip()
    try:
        dt = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()             # attach local tz
        return dt.isoformat()
    except Exception:
        return s


def calendar_create(title: str, start: str, end: str = "",
                    attendees: list | None = None, description: str = "") -> dict:
    """Create a calendar event. For events with OTHER attendees, the caller
    confirms with the Boss first (his rule). Never deletes anything."""
    svc = _service("calendar", "v3")
    if svc is None:
        return {"ok": False, "error": "Google is not set up."}
    try:
        import datetime
        s = _normalize_dt(start)
        if end:
            e = _normalize_dt(end)
        else:
            dt = datetime.datetime.fromisoformat(s)
            e = (dt + datetime.timedelta(hours=1)).isoformat()
        body = {"summary": title, "description": description,
                "start": {"dateTime": s}, "end": {"dateTime": e}}
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        ev = svc.events().insert(
            calendarId="primary", body=body,
            sendUpdates="all" if attendees else "none").execute()
        return {"ok": True, "id": ev.get("id"), "link": ev.get("htmlLink"),
                "when": s}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
