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

# Read-only on purpose: awareness, not action. Adding gmail.send later is a
# deliberate, separate decision.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
CREDS_FILE = ROOT / "google_credentials.json"      # the owner drops this in
TOKEN_FILE = ROOT / "data" / "google_token.json"   # written after consent


def is_setup() -> bool:
    """True once the owner has connected his Google account."""
    return TOKEN_FILE.exists()


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
    if not CREDS_FILE.exists():
        return {"ok": False,
                "error": f"No google_credentials.json in the Jarvis folder. "
                         f"Create an OAuth 'Desktop app' client in Google Cloud "
                         f"(with the Gmail and Calendar APIs enabled), download "
                         f"its JSON, and save it as {CREDS_FILE.name}."}
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
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
