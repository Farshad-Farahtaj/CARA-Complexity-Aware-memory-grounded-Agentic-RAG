import os
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
from database import (
    init_db, get_pending_escalations, set_escalation_telegram_id,
    resolve_escalation_by_telegram_id, get_patient_by_id,
)

load_dotenv()
init_db()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DOCTOR_CHAT_ID = os.getenv("DOCTOR_CHAT_ID")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# A REAL client-side network timeout (seconds). Without this, a stalled network
# call could hang this bot forever with no error and no output - which is exactly
# what silently stopped escalations from being delivered before this fix.
REQUEST_TIMEOUT = 15


def log(message):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


def send_pending_escalations():
    for esc in get_pending_escalations():
        patient = get_patient_by_id(esc["patient_id"])
        conditions = (patient.get("conditions") or "None on file").replace("; ", "\n- ")
        medications = (patient.get("medications") or "None on file").replace("; ", "\n- ")
        text = (
            f"🏥 PATIENT NEEDS YOUR INPUT\n\n"
            f"Patient: {patient['full_name']}\n\n"
            f"QUESTION: \"{esc['question']}\"\n\n"
            f"Flagged because: {esc['reason']}\n\n"
            f"— Relevant history —\n- {conditions}\n\n"
            f"— Current medications —\n- {medications}\n\n"
            f"— Known allergies —\n{patient.get('allergies') or 'None known'}\n\n"
            f"Reply to THIS message with your guidance for the patient."
        )
        try:
            resp = requests.post(
                f"{API_URL}/sendMessage",
                json={"chat_id": DOCTOR_CHAT_ID, "text": text},
                timeout=REQUEST_TIMEOUT,
            )
            result = resp.json()
        except requests.exceptions.RequestException as e:
            log(f"Network error sending escalation #{esc['escalation_id']} - will retry next cycle: {e}")
            continue

        if result.get("ok"):
            message_id = result["result"]["message_id"]
            set_escalation_telegram_id(esc["escalation_id"], message_id)
            log(f"Sent escalation #{esc['escalation_id']} to doctor (message_id={message_id})")
        else:
            log(f"Failed to send escalation #{esc['escalation_id']}: {result}")


def check_for_replies(last_update_id):
    try:
        resp = requests.get(
            f"{API_URL}/getUpdates",
            params={"offset": last_update_id + 1, "timeout": 10},
            timeout=REQUEST_TIMEOUT,
        )
        result = resp.json()
    except requests.exceptions.RequestException as e:
        log(f"Network error checking for replies - will retry next cycle: {e}")
        return last_update_id

    if not result.get("ok"):
        log(f"Telegram getUpdates returned an error: {result}")
        return last_update_id
    for update in result["result"]:
        last_update_id = update["update_id"]
        message = update.get("message")
        if message and "reply_to_message" in message:
            original_id = message["reply_to_message"]["message_id"]
            doctor_reply = message.get("text", "")
            resolve_escalation_by_telegram_id(original_id, doctor_reply)
            log(f"Resolved escalation linked to telegram message {original_id}")
    return last_update_id


if __name__ == "__main__":
    if not BOT_TOKEN or not DOCTOR_CHAT_ID:
        print("ERROR: TELEGRAM_BOT_TOKEN or DOCTOR_CHAT_ID missing from .env")
    else:
        print("Telegram escalation bot started. Watching for pending escalations...")
        last_update_id = 0
        loop_count = 0
        while True:
            try:
                send_pending_escalations()
                last_update_id = check_for_replies(last_update_id)
            except Exception as e:
                # Catch-all so one unexpected error never permanently kills the bot -
                # it just logs and tries again on the next cycle.
                log(f"Unexpected error, continuing: {e}")
            loop_count += 1
            if loop_count % 20 == 0:
                log("still watching for escalations...")
            time.sleep(3)