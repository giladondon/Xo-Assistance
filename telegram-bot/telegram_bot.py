import os
import re
import json
import asyncio
import traceback
from pathlib import Path

import aiohttp.web as web
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
from anthropic import Anthropic

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

from helpers.colors import emoji_for_color

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
ai_client = Anthropic()
ACCESS_CODE = os.getenv("BOT_ACCESS_CODE", "")

BASE_DIR = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo("Asia/Jerusalem")
TOKEN_DIR = BASE_DIR / "tokens"
APPROVED_USERS_FILE = TOKEN_DIR / "approved_users.json"


def load_approved_users() -> set:
    if not APPROVED_USERS_FILE.exists():
        return set()
    try:
        with open(APPROVED_USERS_FILE, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_approved_user(user_id: int, bot_data: dict) -> None:
    TOKEN_DIR.mkdir(exist_ok=True)
    approved = bot_data.setdefault("approved_users", load_approved_users())
    approved.add(user_id)
    with open(APPROVED_USERS_FILE, "w") as f:
        json.dump(list(approved), f)


def is_user_approved(user_id: int, bot_data: dict) -> bool:
    if "approved_users" not in bot_data:
        bot_data["approved_users"] = load_approved_users()
    return user_id in bot_data["approved_users"]

with open(BASE_DIR / "notification_templates.json", "r", encoding="utf-8") as f:
    TEMPLATES = json.load(f)


def extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No JSON found in response: {text[:200]}")


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

    if not is_user_approved(user_id, context.bot_data):
        if text.strip() == ACCESS_CODE and ACCESS_CODE:
            save_approved_user(user_id, context.bot_data)
            await update.message.reply_text("✅ קוד אושר! ברוך הבא לבוט.")
        else:
            await update.message.reply_text("🔒 הבוט מוגן. אנא הכנס את קוד הגישה.")
        return

    service = authenticate_google_calendar(user_id)
    if not service:
        try:
            auth_url, flow = start_auth_flow(user_id)
        except Exception as e:
            await update.message.reply_text(f"❌ שגיאה בתהליך ההרשאה: {e}")
            return
        context.bot_data.setdefault("pending_auth", {})[user_id] = {
            "chat_id": update.effective_chat.id,
            "flow": flow,
        }
        await update.message.reply_text(
            f"👋 כדי להשתמש בבוט יש לאשר גישה ליומן:\n{auth_url}\n\nלאחר האישור תקבל הודעה אוטומטית כאן."
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

    with open(BASE_DIR / "xo_assistance_prompt.txt", "r", encoding="utf-8") as f:
        base = f.read()
    system_prompt = base

    today = datetime.now().strftime("%Y-%m-%d")

    try:
        resp = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": f"התאריך היום הוא {today}. הפקודה היא: {text}"}],
            temperature=0,
        )
        data = extract_json(resp.content[0].text)

        action = data.get("action")
        summary = data.get("summary")
        start_time = data.get("start_time")
        duration = data.get("duration_minutes", 60)
        color_id = data.get("color_id", "")

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

        if action == "create":
            create_event(
                service,
                summary,
                start_time,
                duration,
                color_id,
                calendar_id=calendar_id,
            )
            await update.message.reply_text("✅ אירוע נוצר עם צבע לפי הסיווג.")

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
                if color_id:
                    service.events().patch(
                        calendarId=calendar_id,
                        eventId=event["id"],
                        body={"colorId": str(color_id)},
                        sendUpdates="none",
                    ).execute()
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

        # Format event list with exact time range and color emoji per event
        event_lines = []
        for event in events:
            summary = event.get("summary", "ללא כותרת")
            emoji = emoji_for_color(event.get("colorId"))
            start_time = event["start"].get("dateTime")
            end_time = event["end"].get("dateTime")
            if start_time and end_time:
                start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00")).astimezone(LOCAL_TZ)
                time_str = f"{start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')}"
            else:
                all_day = event["start"].get("date")
                time_str = "אירוע יום שלם" if all_day else "זמן לא צוין"
            emoji_suffix = f" {emoji}" if emoji else ""
            event_lines.append(f"{time_str} - {summary}{emoji_suffix}")

        # Load prompt and inject requested date
        date_str = target_date.strftime("%d/%m/%Y")
        with open(BASE_DIR / "summarize_schedule_prompt.txt", "r", encoding="utf-8") as f:
            prompt_template = f.read()
        prompt = prompt_template.replace("[תאריך]", date_str)
        full_prompt = prompt + "\n" + "\n\n".join(event_lines)

        response = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.3,
        )

        summary_text = response.content[0].text
        summary_text = summary_text.replace("\n- ", "\n\n- ").strip()
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


async def oauth_callback(request: web.Request) -> web.Response:
    code = request.rel_url.query.get("code")
    state = request.rel_url.query.get("state")

    if not code or not state:
        return web.Response(text="Missing parameters", status=400)

    try:
        user_id = int(state)
        ptb_app = request.app["ptb_app"]
        pending = ptb_app.bot_data.get("pending_auth", {}).pop(user_id, {})
        flow = pending.get("flow")
        chat_id = pending.get("chat_id")

        finish_auth_flow(user_id, flow, code)

        if chat_id:
            await ptb_app.bot.send_message(
                chat_id=chat_id,
                text="✅ ההרשאה הושלמה! שלח את הפקודה שלך.",
            )

        return web.Response(
            text="<html><body><h2>✅ ההרשאה הושלמה! תוכל לחזור לטלגרם.</h2></body></html>",
            content_type="text/html",
            charset="utf-8",
        )
    except Exception as e:
        traceback.print_exc()
        return web.Response(text=f"Error: {e}", status=500)


async def run():
    ptb_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    ptb_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    ptb_app.job_queue.run_repeating(check_event_changes, interval=60, first=10)

    aiohttp_app = web.Application()
    aiohttp_app["ptb_app"] = ptb_app
    aiohttp_app.router.add_get("/oauth/callback", oauth_callback)

    runner = web.AppRunner(aiohttp_app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    async with ptb_app:
        await ptb_app.start()
        await ptb_app.updater.start_polling()
        print("🤖 הבוט מחובר לטלגרם ומחכה להודעות...")
        try:
            await asyncio.Event().wait()
        finally:
            await ptb_app.updater.stop()
            await ptb_app.stop()
            await runner.cleanup()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
