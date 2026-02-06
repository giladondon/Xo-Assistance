from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import os
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = Path(
    os.getenv("GOOGLE_CREDENTIALS_FILE", BASE_DIR / "credentials.json")
)

# Define Google Calendar Access Scope
SCOPES = ['https://www.googleapis.com/auth/calendar']
TOKEN_DIR = BASE_DIR / "tokens"
CALENDAR_PREF_PREFIX = "calendar_"
_oauth_server = None
_oauth_server_lock = threading.Lock()


def _resolve_redirect_uri() -> str | None:
    """Return redirect URI from env or credentials.json.

    Priority is given to the ``GOOGLE_REDIRECT_URI`` environment variable. If it
    is missing, the first URI from ``credentials.json`` (web or installed block)
    is used. ``None`` is returned if neither source provides a URI.
    """
    env_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if env_uri:
        return env_uri
    try:
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for block in ("web", "installed"):
            uris = data.get(block, {}).get("redirect_uris")
            if uris:
                return uris[0]
    except Exception:
        pass
    return None


def _build_oauth_callback_html(code: str | None) -> bytes:
    safe_code = code or ""
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Authorization complete</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 2rem; line-height: 1.5; }}
    .card {{ max-width: 720px; border: 1px solid #d9d9d9; border-radius: 12px; padding: 1rem 1.25rem; }}
    .code {{ display: block; width: 100%; margin-top: .5rem; padding: .75rem; font-family: monospace; font-size: 0.95rem; }}
    button {{ margin-top: .75rem; padding: .6rem 1rem; border-radius: 8px; border: 1px solid #999; cursor: pointer; }}
  </style>
</head>
<body>
  <div class="card">
    <h2>✅ ההתחברות לגוגל הושלמה</h2>
    <p>העתק/י את הקוד הבא והדבק/י אותו בבוט בטלגרם.</p>
    <input id="oauthCode" class="code" value="{safe_code}" readonly />
    <button type="button" onclick="copyCode()">Copy code</button>
    <p id="copied"></p>
  </div>
  <script>
    function copyCode() {{
      const field = document.getElementById('oauthCode');
      field.select();
      field.setSelectionRange(0, 99999);
      navigator.clipboard.writeText(field.value).then(() => {{
        document.getElementById('copied').innerText = 'הועתק בהצלחה. אפשר לחזור עכשיו לטלגרם.';
      }});
    }}
  </script>
</body>
</html>
"""
    return body.encode("utf-8")


def _start_oauth_callback_server() -> None:
    global _oauth_server

    redirect_uri = _resolve_redirect_uri()
    if not redirect_uri:
        return

    parsed = urlparse(redirect_uri)
    if parsed.scheme != "http":
        return

    port = parsed.port or 80
    expected_path = parsed.path or "/"

    with _oauth_server_lock:
        if _oauth_server is not None:
            return

        class OAuthCallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                incoming = urlparse(self.path)
                if (incoming.path or "/") != expected_path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return

                code = parse_qs(incoming.query).get("code", [""])[0]
                payload = _build_oauth_callback_html(code)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):
                return

        try:
            _oauth_server = ThreadingHTTPServer(("0.0.0.0", port), OAuthCallbackHandler)
            thread = threading.Thread(target=_oauth_server.serve_forever, daemon=True)
            thread.start()
        except OSError:
            _oauth_server = None


# Resolve redirect URI at call time to capture late environment changes


# Set up OPENAI API
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def authenticate_google_calendar(user_id: int | None = None):
    """Return an authenticated Google Calendar service for the given user.

    If ``user_id`` is ``None`` the legacy single-user token is used.
    Otherwise the token is loaded from ``tokens/token_<user_id>.json``.
    When no token is found ``None`` is returned.
    """
    if user_id is None:
        token_path = BASE_DIR / "token.json"
    else:
        TOKEN_DIR.mkdir(exist_ok=True)
        token_path = TOKEN_DIR / f"token_{user_id}.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds:
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except RefreshError:
                    # Stored credentials are no longer valid; remove the token so a
                    # new OAuth flow can be initiated.
                    try:
                        token_path.unlink()
                    except OSError:
                        pass
                    return None
            elif creds.expired and not creds.refresh_token:
                # Cannot refresh without a refresh token; force re-authentication.
                return None

    if not creds:
        return None

    return build('calendar', 'v3', credentials=creds)


def start_auth_flow():
    """Create an OAuth flow and return the authorization URL and flow object."""
    _start_oauth_callback_server()
    redirect_uri = _resolve_redirect_uri()
    kwargs = {"redirect_uri": redirect_uri} if redirect_uri else {}
    flow = InstalledAppFlow.from_client_secrets_file(
        str(CREDENTIALS_FILE), SCOPES, **kwargs
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    return auth_url, flow

def finish_auth_flow(user_id: int, flow: InstalledAppFlow, code: str):
    """Complete OAuth flow using the provided code and store credentials."""
    redirect_uri = _resolve_redirect_uri()
    if redirect_uri:
        # The flow already has a redirect URI from ``start_auth_flow``; passing it
        # again to ``fetch_token`` results in oauthlib receiving duplicate
        # parameters and raising a ``TypeError``. Ensure the stored redirect URI
        # is used and fetch the token without re-supplying it.
        flow.redirect_uri = redirect_uri
    flow.fetch_token(code=code)
    creds = flow.credentials
    TOKEN_DIR.mkdir(exist_ok=True)
    token_path = TOKEN_DIR / f"token_{user_id}.json"
    with open(token_path, "w") as token_file:
        token_file.write(creds.to_json())
    return build('calendar', 'v3', credentials=creds)


def _calendar_pref_path(user_id: int) -> Path:
    TOKEN_DIR.mkdir(exist_ok=True)
    return TOKEN_DIR / f"{CALENDAR_PREF_PREFIX}{user_id}.json"


def store_user_calendar_id(user_id: int, calendar_id: str) -> None:
    path = _calendar_pref_path(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"calendar_id": calendar_id}, f)


def load_user_calendar_id(user_id: int) -> str | None:
    path = _calendar_pref_path(user_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        calendar_id = data.get("calendar_id")
        if isinstance(calendar_id, str) and calendar_id:
            return calendar_id
    except (json.JSONDecodeError, OSError):
        return None
    return None


def list_calendars(service):
    calendars = []
    page_token = None
    while True:
        response = service.calendarList().list(pageToken=page_token).execute()
        items = response.get("items", [])
        for item in items:
            calendars.append(
                {
                    "id": item.get("id"),
                    "summary": item.get("summary", item.get("id", "")),
                    "primary": item.get("primary", False),
                }
            )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return [cal for cal in calendars if cal.get("id")]


# Create an Event
def create_event(
    service,
    summary,
    start_time_str,
    duration_minutes=60,
    color_id="",
    calendar_id="primary",
):
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
    end_time = start_time + timedelta(minutes=duration_minutes)

    body = {
        "summary": summary,
        "start": {"dateTime": start_time.isoformat(), "timeZone": "Asia/Jerusalem"},
        "end":   {"dateTime": end_time.isoformat(),   "timeZone": "Asia/Jerusalem"},
    }

    if color_id:
        body["colorId"] = str(color_id)

    event = service.events().insert(
        calendarId=calendar_id,
        body=body,
        sendUpdates="none"
    ).execute()
    return event

def find_event(service, user_text, calendar_id="primary"):
    """
    Searches for an event based on partial text match in the upcoming 7 days.
    Returns the best matching event or None.
    """
    now = datetime.utcnow().isoformat() + "Z"
    later = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId=calendar_id,
        timeMin=now,
        timeMax=later,
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])
    matches = []

    for event in events:
        summary = event.get("summary", "")
        if summary and user_text.strip() in summary:
            matches.append(event)

    if matches:
        return matches[0]  # First match for now (can improve later)

    return None

def delete_event(service, event_id, calendar_id="primary"):
    """
    Deletes an event by ID.
    """
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    print("🗑️ האירוע נמחק בהצלחה.")

def update_event(service, event_id, updates, calendar_id="primary"):
    """
    Updates an existing event with new values.
    updates = {
        "summary": "...",
        "start_time": "YYYY-MM-DD HH:MM",
        "duration_minutes": ...
    }
    """
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    if "summary" in updates:
        event['summary'] = updates['summary']

    if "start_time" in updates:
        start = datetime.strptime(updates["start_time"], "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=updates.get("duration_minutes", 60))
        event['start'] = {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"}
        event['end'] = {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"}

    updated_event = service.events().update(
        calendarId=calendar_id, eventId=event_id, body=event
    ).execute()
    print("✅ האירוע עודכן בהצלחה.")

# Clean Json File
def extract_json(text):
    match = re.search(r"\{.*}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    else:
        raise ValueError("❌ לא נמצא JSON תקני")

# Parse text with GPT module
def parse_with_gpt(text):
    with open(BASE_DIR / "xo_assistance_prompt.txt", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    today = datetime.today().strftime("%Y-%m-%d")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"התאריך היום הוא {today}. הפקודה היא: {text}"}
        ],
    )

    response_text = response.choices[0].message.content
    parsed = extract_json(response_text)

    return parsed

# Main program flow
if __name__ == "__main__":
    service = authenticate_google_calendar()

    hebrew_input = input("הכנס תיאור של האירוע בעברית:\n")
    try:
        parsed = parse_with_gpt(hebrew_input)
        create_event(service, parsed["summary"], parsed["start_time"], parsed.get("duration_minutes", 60))
    except Exception as e:
        print("❌ שגיאה:", e)
