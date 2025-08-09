import os
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)
from datetime import datetime, timedelta
from openai import OpenAI

from create_event import (
    authenticate_google_calendar,
    create_event,
    find_event,
    delete_event,
    update_event,
)

from helpers.contacts import load_contacts, all_labels, emails_for_label
from helpers.colors import color_for_label

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

with open("notification_templates.json", "r", encoding="utf-8") as f:
    TEMPLATES = json.load(f)


def render_message(key, **kwargs):
    template = TEMPLATES.get(key, "")
    return template.format(**kwargs)


def within_next_24h(start_str: str) -> bool:
    try:
        if "T" in start_str:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(start_str)
    except ValueError:
        return False
    diff = (dt - datetime.utcnow()).total_seconds()
    return 0 <= diff <= 24 * 3600


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.bot_data.setdefault("chat_id", update.effective_chat.id)
    text = update.message.text
    await update.message.reply_text("🧠 מעבד את הפקודה...")

    pending = context.user_data.get("pending_event")
    if pending:
        chosen = update.message.text.strip()
        if chosen not in all_labels():
            await update.message.reply_text(f"תגית לא מוכרת. נסה אחת מ: {', '.join(all_labels())}")
            return
        service = authenticate_google_calendar()
        if pending["action"] == "create":
            create_event(service, pending["summary"], pending["start"], pending["duration"], chosen)
            await update.message.reply_text("✅ אירוע נוצר עם התגית, צבע והזמנות נשלחו.")
        else:
            # update flow: fuzzy find, update time/summary, then patch attendees/color
            ev = find_event(service, pending["summary"])  # use your fuzzy finder
            if not ev:
                await update.message.reply_text("❌ לא נמצא אירוע לעדכון.")
            else:
                # update existing fields you already support:
                updates = {
                    "summary": pending["summary"],
                    "start_time": pending["start"],
                    "duration_minutes": pending["duration"],
                }
                update_event(service, ev["id"], updates)
                patch = {}
                ems = emails_for_label(chosen)
                if ems: patch["attendees"] = [{"email": e} for e in ems]
                cid = color_for_label(chosen)
                if cid: patch["colorId"] = cid
                if patch:
                    service.events().patch(
                        calendarId="primary", eventId=ev["id"], body=patch, sendUpdates="all"
                    ).execute()
                await update.message.reply_text("✏️ האירוע עודכן עם תגית, צבע והזמנות.")
        context.user_data.pop("pending_event", None)
        return

    # Build prompt
    with open("xo_assistance_prompt.txt", "r", encoding="utf-8") as f:
        base = f.read()
    system_prompt = base.replace("{LABELS}", ", ".join(all_labels()))

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        # GPT call
        client = OpenAI(api_key=OPENAI_API_KEY)
        resp = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"התאריך היום הוא {today}. הפקודה היא: {text}"}
            ],
            temperature=0
        )
        data = json.loads(resp.choices[0].message.content)

        action = data.get("action")
        summary = data.get("summary")
        start_time = data.get("start_time")
        duration = data.get("duration_minutes", 60)
        label = (data.get("label") or "").strip()

        service = authenticate_google_calendar()

        if action in ("create", "update") and not label:
            context.user_data["pending_event"] = {
                "action": action, "summary": summary, "start": start_time, "duration": duration
            }
            await update.message.reply_text(f"לא זיהיתי תגית. בחר אחת: {', '.join(all_labels())}")
            return

        if action == "summarize":
            await send_tomorrow_schedule(update, context)
            return

        if action == "create":
            create_event(service, summary, start_time, duration, label)
            await update.message.reply_text("✅ אירוע נוצר עם צבע והזמנות (אם קיימות).")

        elif action == "delete":
            event = find_event(service, summary)
            if event:
                delete_event(service, event["id"])
                await update.message.reply_text("🗑️ האירוע נמחק בהצלחה!")
            else:
                await update.message.reply_text("❌ לא נמצא אירוע למחיקה.")

        elif action == "update":
            event = find_event(service, summary)
            if event:
                update_event(service, event["id"], data)
                await update.message.reply_text("✏️ האירוע עודכן בהצלחה!")
            else:
                await update.message.reply_text("❌ לא נמצא אירוע לעדכון.")

        else:
            await update.message.reply_text("❌ פעולה לא מזוהה.")

    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {str(e)}")


async def send_tomorrow_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        service = authenticate_google_calendar()

        # Get tomorrow's range
        tomorrow = datetime.utcnow() + timedelta(days=1)
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day)
        end = start + timedelta(days=1)

        events_result = service.events().list(
            calendarId='primary',
            timeMin=start.isoformat() + "Z",
            timeMax=end.isoformat() + "Z",
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get("items", [])
        if not events:
            await update.message.reply_text("📭 אין אירועים מחר.")
            return

        # Format event list
        event_lines = []
        for event in events:
            summary = event.get("summary", "ללא כותרת")
            start_time = event["start"].get("dateTime", event["start"].get("date"))
            duration = 60  # Default
            if "dateTime" in event["start"] and "dateTime" in event["end"]:
                start_dt = datetime.fromisoformat(event["start"]["dateTime"])
                end_dt = datetime.fromisoformat(event["end"]["dateTime"])
                duration = int((end_dt - start_dt).total_seconds() // 60)
                time_str = start_dt.strftime("%H:%M")
            else:
                time_str = start_time
            event_lines.append(f"{time_str} - {summary} (משך: {duration} דקות)")

        # Load prompt
        with open("summarize_schedule_prompt.txt", "r", encoding="utf-8") as f:
            prompt = f.read()
        full_prompt = prompt + "\n" + "\n".join(event_lines)

        # GPT call
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3
        )

        summary_text = response.choices[0].message.content
        await update.message.reply_text(summary_text)

    except Exception as e:
        await update.message.reply_text(f"❌ שגיאה: {str(e)}")


async def check_event_changes(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.bot_data.get("chat_id")
    if not chat_id:
        return

    service = authenticate_google_calendar()
    now = datetime.utcnow()
    time_min = now.isoformat() + "Z"
    time_max = (now + timedelta(hours=24)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])
    tracked = context.bot_data.setdefault("tracked_events", {})
    current_ids = set()

    for ev in events:
        ev_id = ev["id"]
        current_ids.add(ev_id)
        start = ev["start"].get("dateTime", ev["start"].get("date"))
        summary = ev.get("summary", "ללא כותרת")
        updated = ev.get("updated")
        if ev_id not in tracked:
            tracked[ev_id] = {"updated": updated, "summary": summary, "start": start}
        else:
            if tracked[ev_id]["updated"] != updated:
                tracked[ev_id] = {"updated": updated, "summary": summary, "start": start}
                msg = render_message("event_updated", summary=summary, start_time=start)
                await context.bot.send_message(chat_id=chat_id, text=msg)

    removed = [eid for eid in list(tracked.keys()) if eid not in current_ids]
    for eid in removed:
        info = tracked.pop(eid)
        if within_next_24h(info["start"]):
            msg = render_message("event_deleted", summary=info["summary"], start_time=info["start"])
            await context.bot.send_message(chat_id=chat_id, text=msg)


def main():
    LABELS = load_contacts("tag_contacts.xlsx")  # loads and caches labels & emails
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(check_event_changes, interval=60, first=10)
    print("🤖 הבוט מחובר לטלגרם ומחכה להודעות...")
    app.run_polling()


if __name__ == "__main__":
    main()
