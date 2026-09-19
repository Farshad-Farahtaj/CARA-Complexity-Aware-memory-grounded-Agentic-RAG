# -*- coding: utf-8 -*-
"""
Patch script for CARA — fixes the "Check for doctor's reply" bug.

Run this ONCE from the project root:
    C:\\Users\\Markazi.co\\Desktop\\Thesis> python fix_doctor_reply_bug.py

It edits backend\\database.py and backend\\app.py IN PLACE.

It is safe: it will refuse to touch a file if the exact code it expects to
find is missing (for example if you already changed something by hand), so
it can never silently corrupt your files. If it can't apply a change, it
prints exactly which part it couldn't find and changes nothing in that file.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "backend" / "database.py"
APP_FILE = ROOT / "backend" / "app.py"


def patch_file(path, replacements):
    if not path.exists():
        print(f"[X] File not found: {path}")
        return False

    text = path.read_text(encoding="utf-8")
    original_text = text
    all_ok = True

    for label, old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
            print(f"    - applied: {label}")
        elif new in text:
            print(f"    - already applied: {label}")
        else:
            print(f"[X] Could not find the expected code for: {label}")
            print(f"    -> Nothing was changed in {path.name}.")
            print("    -> Copy this whole output and send it back to Claude.")
            all_ok = False

    if not all_ok:
        return False

    if text != original_text:
        path.write_text(text, encoding="utf-8")
        print(f"[OK] Saved changes to {path.name}")
    else:
        print(f"[OK] {path.name} was already up to date - nothing to change.")

    return True


DB_OLD_TABLE = '''        conn.execute("""
            CREATE TABLE IF NOT EXISTS escalations (
                escalation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                patient_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                draft_answer TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                doctor_reply TEXT,
                telegram_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id),
                FOREIGN KEY (patient_id) REFERENCES patients (patient_id)
            )
        """)
        conn.commit()'''

DB_NEW_TABLE = '''        conn.execute("""
            CREATE TABLE IF NOT EXISTS escalations (
                escalation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                patient_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                draft_answer TEXT NOT NULL,
                reason TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                doctor_reply TEXT,
                telegram_message_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                shown_to_patient INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (conversation_id) REFERENCES conversations (conversation_id),
                FOREIGN KEY (patient_id) REFERENCES patients (patient_id)
            )
        """)
        try:
            conn.execute("ALTER TABLE escalations ADD COLUMN shown_to_patient INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB, or this migration already ran before)
        conn.commit()'''

DB_OLD_FUNC = '''def get_escalation_for_conversation(conversation_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM escalations WHERE conversation_id = ? ORDER BY escalation_id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None'''

DB_NEW_FUNC = '''def get_escalation_for_conversation(conversation_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM escalations WHERE conversation_id = ? ORDER BY escalation_id DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        return dict(row) if row else None


def mark_escalation_shown(escalation_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE escalations SET shown_to_patient = 1 WHERE escalation_id = ?",
            (escalation_id,),
        )
        conn.commit()'''

APP_OLD_IMPORT = '''from database import (
    init_db, register_clinic, login_clinic, register_patient, login_patient,
    get_clinic_patients, count_clinic_patients,
    create_conversation, get_patient_conversations, rename_conversation,
    delete_patient_account, save_message, get_chat_history, delete_last_assistant_message,
    reset_patient_password, reset_clinic_password, get_patient_by_id, get_clinic_by_id,
    create_escalation, get_escalation_for_conversation,
)'''

APP_NEW_IMPORT = '''from database import (
    init_db, register_clinic, login_clinic, register_patient, login_patient,
    get_clinic_patients, count_clinic_patients,
    create_conversation, get_patient_conversations, rename_conversation,
    delete_patient_account, save_message, get_chat_history, delete_last_assistant_message,
    reset_patient_password, reset_clinic_password, get_patient_by_id, get_clinic_by_id,
    create_escalation, get_escalation_for_conversation, mark_escalation_shown,
)'''

APP_OLD_BUTTON = '''        pending_escalation = get_escalation_for_conversation(st.session_state.active_conv)
        if pending_escalation and pending_escalation["status"] == "pending":
            if st.button("🔄 Check for doctor's reply"):
                refreshed = get_escalation_for_conversation(st.session_state.active_conv)
                if refreshed["status"] == "resolved":
                    clinic = get_clinic_by_id(patient["clinic_id"])
                    doctor_name = clinic["contact_name"] if clinic else "your doctor"
                    friendly_reply = rephrase_for_patient(refreshed["doctor_reply"])
                    final_msg = f"✅ **Reviewed by {doctor_name}:**\\n\\n{friendly_reply}"
                    save_message(st.session_state.active_conv, "assistant", final_msg)
                    st.rerun()
                else:
                    st.info("No reply from your doctor yet — please check back later.")'''

APP_NEW_BUTTON = '''        pending_escalation = get_escalation_for_conversation(st.session_state.active_conv)
        if pending_escalation and pending_escalation["status"] == "resolved" and not pending_escalation["shown_to_patient"]:
            # Doctor replied since the last time this page rendered — show it now,
            # instead of only checking inside the button click (that's what caused the bug).
            with st.spinner("Fetching your doctor's reply..."):
                clinic = get_clinic_by_id(patient["clinic_id"])
                doctor_name = clinic["contact_name"] if clinic else "your doctor"
                friendly_reply = rephrase_for_patient(pending_escalation["doctor_reply"])
            final_msg = f"✅ **Reviewed by {doctor_name}:**\\n\\n{friendly_reply}"
            save_message(st.session_state.active_conv, "assistant", final_msg)
            mark_escalation_shown(pending_escalation["escalation_id"])
            st.rerun()
        elif pending_escalation and pending_escalation["status"] == "pending":
            if st.button("🔄 Check for doctor's reply"):
                refreshed = get_escalation_for_conversation(st.session_state.active_conv)
                if refreshed["status"] == "pending":
                    st.info("No reply from your doctor yet — please check back later.")
                else:
                    st.rerun()  # now resolved -> the block above will show it on this rerun'''


def main():
    print("=" * 60)
    print("CARA patch: fixing the 'Check for doctor's reply' bug")
    print("=" * 60)

    print("\n[1/2] Patching backend/database.py ...")
    ok_db = patch_file(DB_FILE, [
        ("escalations table + shown_to_patient migration", DB_OLD_TABLE, DB_NEW_TABLE),
        ("mark_escalation_shown() function", DB_OLD_FUNC, DB_NEW_FUNC),
    ])

    print("\n[2/2] Patching backend/app.py ...")
    ok_app = patch_file(APP_FILE, [
        ("database import line", APP_OLD_IMPORT, APP_NEW_IMPORT),
        ("Check for doctor's reply button logic", APP_OLD_BUTTON, APP_NEW_BUTTON),
    ])

    print("\n" + "-" * 60)
    if ok_db and ok_app:
        print("DONE. Now restart both Streamlit and telegram_bot.py to test.")
    else:
        print("Some patches were NOT applied. Nothing was corrupted -")
        print("just copy this whole terminal output and send it to Claude.")
        sys.exit(1)


if __name__ == "__main__":
    main()