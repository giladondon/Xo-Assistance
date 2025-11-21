import os
import json
import re
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from openai import OpenAI

from create_event import (
    authenticate_google_calendar,
    create_event,
    find_event,
    delete_event,
    update_event,
    start_auth_flow,
    finish_auth_flow,
    list_calendars,
    load_user_calendar_id,
    store_user_calendar_id,
)

from helpers.contacts import load_contacts, all_labels, emails_for_label
from helpers.colors import color_for_label, emoji_for_color

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

BASE_DIR = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo("Asia/Jerusalem")

with open(BASE_DIR / "notification_templates.json", "r", encoding="utf-8") as f:
    TEMPLATES = json.load(f)


def render_message(key, **kwargs):
    template = TEMPLATES.get(key, "")
    return template.format(**kwargs)


def time_date_strings(start_str: str):
    try:
        dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
    except ValueError:
        return start_str, start_str
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(LOCAL_TZ)
    return local.strftime("%H:%M"), local.strftime("%d/%m")


def within_next_24h(start_str: str) -> bool:
    try:
        if "T" in start_str:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(start_str)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_utc = dt.astimezone(timezone.utc)
    diff = (dt_utc - datetime.now(timezone.utc)).total_seconds()
    return 0 <= diff <= 24 * 3600


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.bot_data.setdefault("chat_id", update.effective_chat.id)
    context.bot_data["user_id"] = update.effective_user.id
    user_id = context.bot_data["user_id"]
    text = update.message.text

    service = authenticate_google_calendar(user_id)
    if not service:
        pending_flow = context.user_data.get("auth_flow")
        if pending_flow:
            code = text.strip()
            if "code=" in code or code.startswith("http"):
                try:
                    parsed = urlparse(code)
                    code = parse_qs(parsed.query).get("code", [""])[0]
                except Exception:
                    code = ""
            if not code:
                await update.message.reply_text("❌ לא נמצא קוד הרשאה בהודעה.")
                return
            try:
                service = finish_auth_flow(user_id, pending_flow, code)
                context.user_data.pop("auth_flow", None)
                await update.message.reply_text("✅ ההרשאה הושלמה! שלח את הפקודה שוב.")
            except Exception as e:
                await update.message.reply_text(f"❌ שגיאה בתהליך ההרשאה: {e}")
            return
        else:
            try:
                auth_url, flow = start_auth_flow()
            except Exception as e:
                await update.message.reply_text(f"❌ שגיאה בתהליך ההרשאה: {e}")
                return
            context.user_data["auth_flow"] = flow
            await update.message.reply_text(
                f"👋 כדי להשתמש בבוט יש לאשר גישה ליומן:\n{auth_url}\nשלח לי את הקישור המלא או את הקוד שתקבל אחרי האישור."
            )
            return

    selection = context.user_data.get("calendar_selection")
    if selection:
        choice = text.strip()
        calendars = selection.get("calendars", [])
        try:
            idx = int(choice)
        except ValueError:
            await update.message.reply_text("אנא שלח את המספר של היומן שבחרת.")
            return
        if not (1 <= idx <= len(calendars)):
            await update.message.reply_text("מספר לא חוקי. נסה שוב בבקשה.")
            return
        chosen = calendars[idx - 1]
        store_user_calendar_id(user_id, chosen["id"])
        context.user_data.pop("calendar_selection", None)
        chosen_name = chosen.get("summary") or chosen.get("id")
        await update.message.reply_text(
            f"📅 היומן '{chosen_name}' נבחר. שלח שוב את הפקודה."
        )
        return

    calendar_id = load_user_calendar_id(user_id) if user_id else "primary"
    if user_id and not calendar_id:
        calendars = list_calendars(service)
        if not calendars:
            await update.message.reply_text("❌ לא נמצאו יומנים בחשבון.")
            return
        context.user_data["calendar_selection"] = {"calendars": calendars}
        lines = []
        for i, cal in enumerate(calendars, start=1):
            name = cal.get("summary") or cal.get("id")
            if cal.get("primary"):
                name = f"{name} (ראשי)"
            lines.append(f"{i}. {name}")
        message = "\n".join(lines)
        await update.message.reply_text(
            "בחר יומן להמשך עבודה ושלח את המספר המתאים:\n" + message
        )
        return

    if not calendar_id:
        calendar_id = "primary"

    await update.message.reply_text("🧠 מעבד את הפקודה...")

    match = re.search(r"@([\w\u0590-\u05FF]+)", text)
    label = match.group(1) if match else None
    if match:
        text = (text[:match.start()] + text[match.end():]).strip()

    pending = context.user_data.get("pending_event")
    if pending:
        chosen = update.message.text.strip()
        if chosen.startswith("@"):
            chosen = chosen[1:]
        chosen = chosen.split()[0]
        if chosen not in all_labels():
            await update.message.reply_text(
                f"תגית לא מוכרת. נסה אחת מ: {', '.join(all_labels())}"
            )
            return
        if pending["action"] == "create":
            create_event(
                service,
                pending["summary"],
                pending["start"],
                pending["duration"],
                chosen,
                calendar_id=calendar_id,
            )
            await update.message.reply_text("✅ אירוע נוצר עם התגית, צבע והזמנות נשלחו.")
        else:
            ev = find_event(service, pending["summary"], calendar_id=calendar_id)
            if not ev:
                await update.message.reply_text("❌ לא נמצא אירוע לעדכון.")
            else:
                updates = {
                    "summary": pending["summary"],
                    "start_time": pending["start"],
                    "duration_minutes": pending["duration"],
                }
                update_event(service, ev["id"], updates, calendar_id=calendar_id)
                patch = {}
                ems = emails_for_label(chosen)
                if ems:
                    patch["attendees"] = [{"email": e} for e in ems]
                cid = color_for_label(chosen)
                if cid:
                    patch["colorId"] = cid
                if patch:
                    service.events().patch(
                        calendarId=calendar_id,
                        eventId=ev["id"],
                        body=patch,
                        sendUpdates="all",
                    ).execute()
                await update.message.reply_text("✏️ האירוע עודכן עם תגית, צבע והזמנות.")
        context.user_data.pop("pending_event", None)
        return

    with open(BASE_DIR / "xo_assistance_prompt.txt", "r", encoding="utf-8") as f:
        base = f.read()
    system_prompt = base.replace("{LABELS}", ", ".join(all_labels()))

    today = datetime.now().strftime("%Y-%m-%d")

    try:
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

        if action == "summarize":
            target_date = data.get("date")
            date_obj = None
            if target_date:
                try:
                    date_obj = datetime.fromisoformat(target_date).date()
                except ValueError:
                    date_obj = None
            if not date_obj:
                date_obj = datetime.now(LOCAL_TZ).date() + timedelta(days=1)
            await send_schedule_for_date(update, context, service, calendar_id, date_obj)
            return

        if action in ("create", "update") and (not label or label not in all_labels()):
            context.user_data["pending_event"] = {
                "action": action, "summary": summary, "start": start_time, "duration": duration,
            }
            await update.message.reply_text(f"לא זיהיתי תגית. בחר אחת: {', '.join(all_labels())}")
            return

        if action == "create":
            create_event(
                service,
                summary,
                start_time,
                duration,
                label,
                calendar_id=calendar_id,
            )
            await update.message.reply_text("✅ אירוע נוצר עם צבע והזמנות (אם קיימות).")

        elif action == "delete":
            event = find_event(service, summary, calendar_id=calendar_id)
            if event:
                delete_event(service, event["id"], calendar_id=calendar_id)
                await update.message.reply_text("🗑️ האירוע נמחק בהצלחה!")
            else:
                await update.message.reply_text("❌ לא נמצא אירוע למחיקה.")

        elif action == "update":
            event = find_event(service, summary, calendar_id=calendar_id)
            if event:
                update_event(service, event["id"], data, calendar_id=calendar_id)
                await update.message.reply_text("✏️ האירוע עודכן בהצלחה!")
            else:
                await update.message.reply_text("❌ לא נמצא אירוע לעדכון.")

        else:
            await update.message.reply_text("❌ פעולה לא מזוהה.")

    except Exception as e:
        error_message = f"❌ שגיאה: {e}"
        print("Error while handling message:", e)
        traceback.print_exc()
        await update.message.reply_text(error_message)


async def send_schedule_for_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    service,
    calendar_id: str,
    target_date,
):
    try:

        start_local = datetime(
            target_date.year, target_date.month, target_date.day, tzinfo=LOCAL_TZ
        )
        end_local = start_local + timedelta(days=1)

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=start_local.astimezone(timezone.utc).isoformat(),
            timeMax=end_local.astimezone(timezone.utc).isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get("items", [])
        if not events:
            await update.message.reply_text(
                f"📭 אין אירועים בתאריך {target_date.strftime('%d/%m/%Y')}."
            )
            return

        # Format event list and collect color emojis
        event_lines = []
        event_emojis = []
        for event in events:
            summary = event.get("summary", "ללא כותרת")
            start_time = event["start"].get("dateTime")
            end_time = event["end"].get("dateTime")
            if start_time and end_time:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                local_start = start_dt.astimezone(LOCAL_TZ)
                time_str = local_start.strftime("%H:%M")
            else:
                all_day = event["start"].get("date")
                time_str = "אירוע יום שלם" if all_day else "זמן לא צוין"
            event_lines.append(f"{time_str} - {summary}")
            event_emojis.append(emoji_for_color(event.get("colorId")))

        # Load prompt and inject requested date
        date_str = target_date.strftime("%d/%m/%Y")
        with open(BASE_DIR / "summarize_schedule_prompt.txt", "r", encoding="utf-8") as f:
            prompt_template = f.read()
        prompt = prompt_template.replace("[תאריך]", date_str)
        full_prompt = prompt + "\n" + "\n\n".join(event_lines)

        # GPT call
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3
        )

        summary_text = response.choices[0].message.content
        summary_text = summary_text.replace("\n- ", "\n\n- ").strip()

        # Append color emoji to each bullet line
        lines = summary_text.splitlines()
        idx = 0
        for i, line in enumerate(lines):
            if line.strip().startswith("-") and idx < len(event_emojis):
                emoji = event_emojis[idx]
                if emoji:
                    lines[i] = f"{line} {emoji}"
                idx += 1
        summary_text = "\n".join(lines)
        await update.message.reply_text(summary_text)

    except Exception as e:
        print("Error while sending schedule summary:", e)
        traceback.print_exc()
        await update.message.reply_text(f"❌ שגיאה: {str(e)}")


async def check_event_changes(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.bot_data.get("chat_id")
    if not chat_id:
        return

    user_id = context.bot_data.get("user_id")
    service = authenticate_google_calendar(user_id) if user_id else None
    if not service:
        return

    calendar_id = load_user_calendar_id(user_id) if user_id else "primary"
    if user_id and not calendar_id:
        return
    if not calendar_id:
        calendar_id = "primary"

    try:
        now = datetime.now(timezone.utc)
        time_min = now.isoformat()
        time_max = (now + timedelta(hours=24)).isoformat()

        events_result = service.events().list(
            calendarId=calendar_id,
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
            previous = tracked.get(ev_id)
            if not previous:
                tracked[ev_id] = {"updated": updated, "summary": summary, "start": start}
            else:
                has_meaningful_change = (
                    previous.get("start") != start or previous.get("summary") != summary
                )
                if has_meaningful_change and previous.get("updated") != updated:
                    old_time, old_date = time_date_strings(previous["start"])
                    new_time, new_date = time_date_strings(start)
                    tracked[ev_id] = {"updated": updated, "summary": summary, "start": start}
                    msg = render_message(
                        "event_updated",
                        summary=summary,
                        old_time=old_time,
                        old_date=old_date,
                        new_time=new_time,
                        new_date=new_date,
                    )
                    await context.bot.send_message(chat_id=chat_id, text=msg)

        removed = [eid for eid in list(tracked.keys()) if eid not in current_ids]
        for eid in removed:
            info = tracked.pop(eid)
            if within_next_24h(info["start"]):
                old_time, old_date = time_date_strings(info["start"])
                msg = render_message(
                    "event_deleted",
                    summary=info["summary"],
                    old_time=old_time,
                    old_date=old_date,
                )
                await context.bot.send_message(chat_id=chat_id, text=msg)

    except Exception as e:
        print("Error while checking event changes:", e)
        traceback.print_exc()
        await context.bot.send_message(chat_id=chat_id, text=f"❌ שגיאה בבדיקת אירועים: {e}")


def main():
    LABELS = load_contacts(BASE_DIR / "tag_contacts.xlsx")  # loads and caches labels & emails
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.job_queue.run_repeating(check_event_changes, interval=60, first=10)
    print("🤖 הבוט מחובר לטלגרם ומחכה להודעות...")
    app.run_polling()


if __name__ == "__main__":
    main()
