from datetime import datetime, timedelta
import re
import os
import json

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
from openai import OpenAI
from dotenv import load_dotenv
from helpers.colors import color_for_label
from helpers.contacts import emails_for_label

# Define Google Calendar Access Scope
SCOPES = ['https://www.googleapis.com/auth/calendar']

#Set-Up OPENAI API
load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Authenticate with Google
def authenticate_google_calendar():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    # ✅ זה מה שיוצר את השירות, לא את ההרשאה
    service = build('calendar', 'v3', credentials=creds)
    return service

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
        from datetime import datetime, timedelta
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
    with open("xo_assistance_prompt.txt", "r", encoding="utf-8") as f:
        system_prompt = f.read()

    today = datetime.today().strftime("%Y-%m-%d")

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"התאריך היום הוא {today}. הפקודה היא: {text}"}
        ]
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