from datetime import datetime, timedelta
import re
import os
import json
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from openai import OpenAI
from dotenv import load_dotenv
from helpers.colors import color_for_label
from helpers.contacts import emails_for_label

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = Path(
    os.getenv("GOOGLE_CREDENTIALS_FILE", BASE_DIR / "credentials.json")
)

# Define Google Calendar Access Scope
SCOPES = ['https://www.googleapis.com/auth/calendar']
TOKEN_DIR = BASE_DIR / "tokens"


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
# Create an Event
def create_event(service, summary, start_time_str, duration_minutes=60, label=""):
    start_time = datetime.strptime(start_time_str, "%Y-%m-%d %H:%M")
    end_time = start_time + timedelta(minutes=duration_minutes)

    body = {
        "summary": summary,
        "start": {"dateTime": start_time.isoformat(), "timeZone": "Asia/Jerusalem"},
        "end":   {"dateTime": end_time.isoformat(),   "timeZone": "Asia/Jerusalem"},
    }

    # Color by label
    cid = color_for_label(label)
    if cid:
        body["colorId"] = cid

    # Invite label contacts
    emails = emails_for_label(label)
    if emails:
        body["attendees"] = [{"email": e} for e in emails]

    event = service.events().insert(
        calendarId="primary",
        body=body,
        sendUpdates="all" if emails else "none"
    ).execute()
    return event

def find_event(service, user_text):
    """
    Searches for an event based on partial text match in the upcoming 7 days.
    Returns the best matching event or None.
    """
    now = datetime.utcnow().isoformat() + "Z"
    later = (datetime.utcnow() + timedelta(days=7)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId='primary',
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

def delete_event(service, event_id):
    """
    Deletes an event by ID.
    """
    service.events().delete(calendarId='primary', eventId=event_id).execute()
    print("🗑️ האירוע נמחק בהצלחה.")

def update_event(service, event_id, updates):
    """
    Updates an existing event with new values.
    updates = {
        "summary": "...",
        "start_time": "YYYY-MM-DD HH:MM",
        "duration_minutes": ...
    }
    """
    event = service.events().get(calendarId='primary', eventId=event_id).execute()

    if "summary" in updates:
        event['summary'] = updates['summary']

    if "start_time" in updates:
        start = datetime.strptime(updates["start_time"], "%Y-%m-%d %H:%M")
        end = start + timedelta(minutes=updates.get("duration_minutes", 60))
        event['start'] = {"dateTime": start.isoformat(), "timeZone": "Asia/Jerusalem"}
        event['end'] = {"dateTime": end.isoformat(), "timeZone": "Asia/Jerusalem"}

    updated_event = service.events().update(calendarId='primary', eventId=event_id, body=event).execute()
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
