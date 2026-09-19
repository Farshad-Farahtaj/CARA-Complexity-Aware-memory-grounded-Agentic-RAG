# -*- coding: utf-8 -*-
"""
Patch script for CARA - adds clinic-side patient management features:
manual add, CSV/TSV/Excel bulk import + bulk update, patient edit/deactivate,
CSV export, and an in-app Escalations tab (reply without Telegram).

Run this ONCE from the project root:
    C:\\Users\\Markazi.co\\Desktop\\Thesis> python add_clinic_features.py

It edits backend\\database.py and backend\\app.py IN PLACE.
It is safe: it will refuse to touch a file if the exact code it expects to
find is missing, so it can never silently corrupt your files. If it can't
apply a change, it prints exactly which part it couldn't find and changes
nothing in that file.
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


# ══════════════════════════════════════════════════════════════════
# backend/database.py patches
# ══════════════════════════════════════════════════════════════════

DB_OLD_PATIENTS_TABLE = '''        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                conditions TEXT DEFAULT '',
                medications TEXT DEFAULT '',
                allergies TEXT DEFAULT '',
                FOREIGN KEY (clinic_id) REFERENCES clinics (clinic_id)
            )
        """)'''

DB_NEW_PATIENTS_TABLE = '''        conn.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
                clinic_id INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                conditions TEXT DEFAULT '',
                medications TEXT DEFAULT '',
                allergies TEXT DEFAULT '',
                account_claimed INTEGER NOT NULL DEFAULT 1,
                is_active INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (clinic_id) REFERENCES clinics (clinic_id)
            )
        """)'''

DB_OLD_MIGRATION_TAIL = '''        try:
            conn.execute("ALTER TABLE escalations ADD COLUMN shown_to_patient INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB, or this migration already ran before)
        conn.commit()'''

DB_NEW_MIGRATION_TAIL = '''        try:
            conn.execute("ALTER TABLE escalations ADD COLUMN shown_to_patient INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # column already exists (fresh DB, or this migration already ran before)
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN account_claimed INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE patients ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        except sqlite3.OperationalError:
            pass
        conn.commit()'''

DB_OLD_REGISTER_PATIENT = '''def register_patient(clinic_id, full_name, email, password, conditions="", medications="", allergies=""):
    with get_conn() as conn:
        clinic = conn.execute("SELECT 1 FROM clinics WHERE clinic_id = ?", (clinic_id,)).fetchone()
        if not clinic:
            return False, "No clinic found with that Clinic ID."
        try:
            conn.execute(
                "INSERT INTO patients (clinic_id, full_name, email, password_hash, conditions, medications, allergies) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (clinic_id, full_name, email.lower().strip(), hash_password(password), conditions, medications, allergies),
            )
            conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, "An account with this email already exists."'''

DB_NEW_REGISTER_PATIENT = '''def register_patient(clinic_id, full_name, email, password, conditions="", medications="", allergies=""):
    email = email.lower().strip()
    with get_conn() as conn:
        clinic = conn.execute("SELECT 1 FROM clinics WHERE clinic_id = ?", (clinic_id,)).fetchone()
        if not clinic:
            return False, "No clinic found with that Clinic ID."

        # If the clinic already pre-loaded this patient (manual add / CSV import) but they
        # have not set a password yet, this registration "claims" that existing record
        # instead of creating a duplicate.
        shell = conn.execute(
            "SELECT patient_id FROM patients WHERE email = ? AND clinic_id = ? AND account_claimed = 0",
            (email, clinic_id),
        ).fetchone()
        if shell:
            conn.execute(
                "UPDATE patients SET password_hash = ?, full_name = ?, account_claimed = 1, is_active = 1 "
                "WHERE patient_id = ?",
                (hash_password(password), full_name, shell["patient_id"]),
            )
            conn.commit()
            return True, None

        try:
            conn.execute(
                "INSERT INTO patients "
                "(clinic_id, full_name, email, password_hash, conditions, medications, allergies, account_claimed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (clinic_id, full_name, email, hash_password(password), conditions, medications, allergies),
            )
            conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, "An account with this email already exists."'''

DB_OLD_LOGIN_PATIENT = '''def login_patient(email, password):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM patients WHERE email = ?", (email.lower().strip(),)).fetchone()
        if row and check_password(password, row["password_hash"]):
            return dict(row)
        return None'''

DB_NEW_LOGIN_PATIENT = '''def login_patient(email, password):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM patients WHERE email = ?", (email.lower().strip(),)).fetchone()
        if row and row["password_hash"] and check_password(password, row["password_hash"]):
            return dict(row)
        return None'''

DB_OLD_GET_CLINIC_PATIENTS = '''def get_clinic_patients(clinic_id):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM patients WHERE clinic_id = ?", (clinic_id,)).fetchall()
        return [dict(r) for r in rows]'''

DB_NEW_GET_CLINIC_PATIENTS = '''def get_clinic_patients(clinic_id, active_only=True):
    with get_conn() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM patients WHERE clinic_id = ? AND is_active = 1 ORDER BY full_name",
                (clinic_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM patients WHERE clinic_id = ? ORDER BY is_active DESC, full_name",
                (clinic_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def add_patient_shell(clinic_id, full_name, email, conditions="", medications="", allergies=""):
    """Clinic pre-loads a patient with no password yet (manual add or CSV import).
    The patient later "claims" the account by registering with this same email -
    see register_patient(), which detects the unclaimed shell and completes it."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO patients "
                "(clinic_id, full_name, email, password_hash, conditions, medications, allergies, account_claimed) "
                "VALUES (?, ?, ?, '', ?, ?, ?, 0)",
                (clinic_id, full_name.strip(), email.lower().strip(), conditions, medications, allergies),
            )
            conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, f"A patient with the email {email} already exists."


def update_patient(patient_id, full_name=None, conditions=None, medications=None, allergies=None):
    fields, values = [], []
    for col, val in [("full_name", full_name), ("conditions", conditions),
                      ("medications", medications), ("allergies", allergies)]:
        if val is not None:
            fields.append(f"{col} = ?")
            values.append(val)
    if not fields:
        return
    values.append(patient_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE patients SET {', '.join(fields)} WHERE patient_id = ?", values)
        conn.commit()


def set_patient_active(patient_id, is_active):
    with get_conn() as conn:
        conn.execute(
            "UPDATE patients SET is_active = ? WHERE patient_id = ?",
            (1 if is_active else 0, patient_id),
        )
        conn.commit()


def get_patient_by_email(clinic_id, email):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE clinic_id = ? AND email = ?",
            (clinic_id, email.lower().strip()),
        ).fetchone()
        return dict(row) if row else None'''

DB_OLD_COUNT_CLINIC_PATIENTS = '''def count_clinic_patients(clinic_id):
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM patients WHERE clinic_id = ?", (clinic_id,)).fetchone()
        return row["n"]'''

DB_NEW_COUNT_CLINIC_PATIENTS = '''def count_clinic_patients(clinic_id, active_only=True):
    with get_conn() as conn:
        if active_only:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM patients WHERE clinic_id = ? AND is_active = 1", (clinic_id,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS n FROM patients WHERE clinic_id = ?", (clinic_id,)).fetchone()
        return row["n"]'''

DB_OLD_MARK_SHOWN = '''def mark_escalation_shown(escalation_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE escalations SET shown_to_patient = 1 WHERE escalation_id = ?",
            (escalation_id,),
        )
        conn.commit()'''

DB_NEW_MARK_SHOWN = '''def mark_escalation_shown(escalation_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE escalations SET shown_to_patient = 1 WHERE escalation_id = ?",
            (escalation_id,),
        )
        conn.commit()


def get_escalations_for_clinic(clinic_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT e.*, p.full_name AS patient_name "
            "FROM escalations e JOIN patients p ON e.patient_id = p.patient_id "
            "WHERE p.clinic_id = ? "
            "ORDER BY (e.status = 'pending') DESC, e.created_at DESC",
            (clinic_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def resolve_escalation_manually(escalation_id, doctor_reply):
    with get_conn() as conn:
        conn.execute(
            "UPDATE escalations SET status = 'resolved', doctor_reply = ?, resolved_at = CURRENT_TIMESTAMP "
            "WHERE escalation_id = ?",
            (doctor_reply, escalation_id),
        )
        conn.commit()'''

# ══════════════════════════════════════════════════════════════════
# backend/app.py patches
# ══════════════════════════════════════════════════════════════════

APP_OLD_TOP_IMPORTS = '''import html
import os
import streamlit as st
import markdown as md_lib
import pypdf
from groq import Groq as GroqClient'''

APP_NEW_TOP_IMPORTS = '''import html
import os
import streamlit as st
import pandas as pd
import markdown as md_lib
import pypdf
from groq import Groq as GroqClient'''

APP_OLD_DB_IMPORT = '''from database import (
    init_db, register_clinic, login_clinic, register_patient, login_patient,
    get_clinic_patients, count_clinic_patients,
    create_conversation, get_patient_conversations, rename_conversation,
    delete_patient_account, save_message, get_chat_history, delete_last_assistant_message,
    reset_patient_password, reset_clinic_password, get_patient_by_id, get_clinic_by_id,
    create_escalation, get_escalation_for_conversation, mark_escalation_shown,
)'''

APP_NEW_DB_IMPORT = '''from database import (
    init_db, register_clinic, login_clinic, register_patient, login_patient,
    get_clinic_patients, count_clinic_patients, add_patient_shell, update_patient,
    set_patient_active, get_patient_by_email,
    create_conversation, get_patient_conversations, rename_conversation,
    delete_patient_account, save_message, get_chat_history, delete_last_assistant_message,
    reset_patient_password, reset_clinic_password, get_patient_by_id, get_clinic_by_id,
    create_escalation, get_escalation_for_conversation, mark_escalation_shown,
    get_escalations_for_clinic, resolve_escalation_manually,
)'''

APP_OLD_CLINIC_DASHBOARD = '''# ── Clinic dashboard ───────────────────────────────────────────────
if st.session_state.user_type == "clinic":
    clinic = st.session_state.user

    with st.sidebar:
        brand_header()
        st.markdown(f"""<div class="patient-card">
            <div class="patient-card-label">Organization</div>
            <div class="patient-card-name">{clinic['clinic_name']}</div>
        </div>""", unsafe_allow_html=True)
        if st.button("Log out", use_container_width=True):
            do_logout()
            st.rerun()

    st.title(f"🏥 {clinic['clinic_name']}")
    st.caption(f"{clinic['clinic_type']} · Managed by {clinic['contact_name']}")

    n_patients = count_clinic_patients(clinic["clinic_id"])
    stat_a, stat_b, stat_c = st.columns(3)
    stat_a.metric("Registered patients", n_patients)
    stat_b.metric("Clinic ID", clinic["clinic_id"])
    stat_c.metric("Phone", clinic["phone"])

    st.info(f"Share **Clinic ID {clinic['clinic_id']}** with your patients so they can register.")

    st.subheader("Your patients")
    patients = get_clinic_patients(clinic["clinic_id"])
    if patients:
        st.dataframe(
            [{"Name": p["full_name"], "Email": p["email"], "Conditions": p["conditions"] or "—"} for p in patients],
            use_container_width=True,
        )
    else:
        st.info("No patients registered yet.")

    st.caption("Bulk CSV import for patient records is coming in the next step.")
    st.stop()'''

APP_NEW_CLINIC_DASHBOARD = '''# ── Clinic dashboard ───────────────────────────────────────────────
if st.session_state.user_type == "clinic":
    clinic = st.session_state.user

    with st.sidebar:
        brand_header()
        st.markdown(f"""<div class="patient-card">
            <div class="patient-card-label">Organization</div>
            <div class="patient-card-name">{clinic['clinic_name']}</div>
        </div>""", unsafe_allow_html=True)
        if st.button("Log out", use_container_width=True):
            do_logout()
            st.rerun()

    st.title(f"🏥 {clinic['clinic_name']}")
    st.caption(f"{clinic['clinic_type']} · Managed by {clinic['contact_name']}")

    n_patients = count_clinic_patients(clinic["clinic_id"])
    all_escalations = get_escalations_for_clinic(clinic["clinic_id"])
    pending_escalations = [e for e in all_escalations if e["status"] == "pending"]

    stat_a, stat_b, stat_c, stat_d = st.columns(4)
    stat_a.metric("Active patients", n_patients)
    stat_b.metric("Clinic ID", clinic["clinic_id"])
    stat_c.metric("Phone", clinic["phone"])
    stat_d.metric("Pending questions", len(pending_escalations))

    st.info(f"Share **Clinic ID {clinic['clinic_id']}** with your patients so they can register.")

    tab_patients, tab_add, tab_escalations = st.tabs([
        "👥 Patients", "➕ Add patients", f"⏳ Escalations ({len(pending_escalations)})",
    ])

    # ── Patients tab ────────────────────────────────────────────
    with tab_patients:
        show_inactive = st.checkbox("Show deactivated patients too")
        patients = get_clinic_patients(clinic["clinic_id"], active_only=not show_inactive)

        if not patients:
            st.info("No patients yet. Use the 'Add patients' tab to add some.")
        else:
            search = st.text_input("🔍 Search by name or email", key="patient_search")
            filtered = [
                p for p in patients
                if search.lower() in p["full_name"].lower() or search.lower() in p["email"].lower()
            ] if search else patients

            export_rows = [{
                "Full Name": p["full_name"], "Email": p["email"],
                "Conditions": p["conditions"], "Medications": p["medications"],
                "Allergies": p["allergies"],
                "Account status": "Claimed" if p["account_claimed"] else "Awaiting patient sign-up",
                "Active": "Yes" if p["is_active"] else "No",
            } for p in patients]
            csv_bytes = pd.DataFrame(export_rows).to_csv(index=False).encode("utf-8")
            st.download_button("⬇️ Export patient list (CSV)", csv_bytes, "patients_export.csv", "text/csv")

            for p in filtered:
                status_badge = "🟢" if p["account_claimed"] else "🟡 awaiting sign-up"
                active_badge = "" if p["is_active"] else " · 🚫 deactivated"
                with st.expander(f"{status_badge} {p['full_name']} — {p['email']}{active_badge}"):
                    with st.form(f"edit_patient_{p['patient_id']}"):
                        new_name = st.text_input("Full name", p["full_name"], key=f"name_{p['patient_id']}")
                        new_conditions = st.text_area("Conditions", p["conditions"], key=f"cond_{p['patient_id']}")
                        new_meds = st.text_area("Medications", p["medications"], key=f"med_{p['patient_id']}")
                        new_allergies = st.text_area("Allergies", p["allergies"], key=f"alg_{p['patient_id']}")
                        col1, col2 = st.columns(2)
                        save_clicked = col1.form_submit_button("💾 Save changes", use_container_width=True)
                        toggle_label = "🚫 Deactivate" if p["is_active"] else "✅ Reactivate"
                        toggle_clicked = col2.form_submit_button(toggle_label, use_container_width=True)

                        if save_clicked:
                            update_patient(p["patient_id"], full_name=new_name, conditions=new_conditions,
                                           medications=new_meds, allergies=new_allergies)
                            st.success("Saved.")
                            st.rerun()
                        if toggle_clicked:
                            set_patient_active(p["patient_id"], not p["is_active"])
                            st.rerun()

    # ── Add patients tab ────────────────────────────────────────
    with tab_add:
        st.subheader("Add one patient manually")
        st.caption("Good for a handful of patients. The patient sets their own password later "
                   "by registering with this exact email.")
        with st.form("manual_add_patient", clear_on_submit=True):
            m_name = st.text_input("Full name *")
            m_email = st.text_input("Email *")
            m_conditions = st.text_area("Conditions (optional)")
            m_meds = st.text_area("Medications (optional)")
            m_allergies = st.text_area("Allergies (optional)")
            if st.form_submit_button("Add patient", use_container_width=True):
                if not m_name.strip() or not m_email.strip():
                    st.error("Full name and email are required.")
                else:
                    ok, err = add_patient_shell(clinic["clinic_id"], m_name, m_email,
                                                 m_conditions, m_meds, m_allergies)
                    if ok:
                        st.success(f"Added {m_name}. They can now register with {m_email} to set a password.")
                    else:
                        st.error(err)

        st.divider()
        st.subheader("Bulk import from a file")
        st.caption("Accepts .csv, .tsv/.txt, or .xlsx. Required columns: Full Name, Email. "
                   "Optional columns: Conditions, Medications, Allergies.")

        template_df = pd.DataFrame([{
            "Full Name": "Jane Doe", "Email": "jane.doe@example.com",
            "Conditions": "Hypertension; Type 2 diabetes",
            "Medications": "Metformin 500mg; Lisinopril 10mg",
            "Allergies": "Penicillin",
        }])
        st.download_button(
            "⬇️ Download CSV template", template_df.to_csv(index=False).encode("utf-8"),
            "patient_import_template.csv", "text/csv",
        )

        import_mode = st.radio(
            "What should this file do?",
            ["Add new patients", "Update existing patients (matched by email)"],
            horizontal=True,
        )

        uploaded = st.file_uploader("Choose a file", type=["csv", "tsv", "txt", "xlsx"])
        if uploaded is not None:
            df = None
            try:
                if uploaded.name.lower().endswith(".xlsx"):
                    df = pd.read_excel(uploaded)
                elif uploaded.name.lower().endswith((".tsv", ".txt")):
                    df = pd.read_csv(uploaded, sep=None, engine="python")
                else:
                    df = pd.read_csv(uploaded)
            except Exception as e:
                st.error(f"Could not read this file: {e}")

            if df is not None:
                df.columns = [c.strip() for c in df.columns]
                col_map = {c.lower(): c for c in df.columns}
                required = ["full name", "email"]
                missing = [c for c in required if c not in col_map]

                if missing:
                    st.error(f"Missing required column(s): {', '.join(missing)}. "
                             f"Found columns: {', '.join(df.columns)}")
                else:
                    st.write(f"**Preview** ({len(df)} row(s) found):")
                    st.dataframe(df, use_container_width=True)

                    if st.button("✅ Confirm and import", type="primary"):
                        added, updated, skipped = 0, 0, []
                        for i, row in df.iterrows():
                            name = str(row.get(col_map["full name"], "")).strip()
                            email = str(row.get(col_map.get("email"), "")).strip()
                            conditions = str(row.get(col_map.get("conditions", ""), "") or "").strip()
                            meds = str(row.get(col_map.get("medications", ""), "") or "").strip()
                            allergies = str(row.get(col_map.get("allergies", ""), "") or "").strip()

                            if not name or not email or "@" not in email:
                                skipped.append(f"Row {i + 2}: missing/invalid name or email")
                                continue

                            if import_mode == "Add new patients":
                                ok, err = add_patient_shell(clinic["clinic_id"], name, email,
                                                             conditions, meds, allergies)
                                if ok:
                                    added += 1
                                else:
                                    skipped.append(f"Row {i + 2} ({email}): {err}")
                            else:
                                existing = get_patient_by_email(clinic["clinic_id"], email)
                                if not existing:
                                    skipped.append(f"Row {i + 2} ({email}): no existing patient with this email")
                                    continue
                                update_patient(existing["patient_id"], full_name=name,
                                               conditions=conditions, medications=meds, allergies=allergies)
                                updated += 1

                        if added:
                            st.success(f"Added {added} new patient(s).")
                        if updated:
                            st.success(f"Updated {updated} existing patient(s).")
                        if skipped:
                            st.warning("Some rows were skipped:\\n\\n" + "\\n".join(f"- {s}" for s in skipped))
                        if added or updated:
                            st.rerun()

    # ── Escalations tab ──────────────────────────────────────────
    with tab_escalations:
        if not all_escalations:
            st.info("No escalations yet.")
        else:
            for e in all_escalations:
                status_icon = "⏳" if e["status"] == "pending" else "✅"
                title = f"{status_icon} {e['patient_name']} — {e['question'][:60]}..."
                with st.expander(title, expanded=(e["status"] == "pending")):
                    st.markdown(f"**Question:** {e['question']}")
                    st.markdown(f"**Flagged because:** {e['reason']}")
                    st.markdown(f"**CARA's draft answer:** {e['draft_answer']}")
                    if e["status"] == "pending":
                        with st.form(f"reply_{e['escalation_id']}"):
                            reply_text = st.text_area("Your reply to the patient")
                            if st.form_submit_button("Send reply"):
                                if reply_text.strip():
                                    resolve_escalation_manually(e["escalation_id"], reply_text.strip())
                                    st.success("Reply sent. The patient will see it automatically.")
                                    st.rerun()
                                else:
                                    st.error("Please write a reply.")
                    else:
                        st.markdown(f"**Your reply:** {e['doctor_reply']}")

    st.stop()'''


def main():
    print("=" * 60)
    print("CARA patch: clinic patient management + in-app escalations")
    print("=" * 60)

    print()
    print("[1/2] Patching backend/database.py ...")
    ok_db = patch_file(DB_FILE, [
        ("patients table: account_claimed + is_active columns", DB_OLD_PATIENTS_TABLE, DB_NEW_PATIENTS_TABLE),
        ("migration for existing databases", DB_OLD_MIGRATION_TAIL, DB_NEW_MIGRATION_TAIL),
        ("register_patient(): claim-shell-account logic", DB_OLD_REGISTER_PATIENT, DB_NEW_REGISTER_PATIENT),
        ("login_patient(): guard against unclaimed accounts", DB_OLD_LOGIN_PATIENT, DB_NEW_LOGIN_PATIENT),
        ("get_clinic_patients() + add_patient_shell() + update_patient() + set_patient_active() + get_patient_by_email()",
         DB_OLD_GET_CLINIC_PATIENTS, DB_NEW_GET_CLINIC_PATIENTS),
        ("count_clinic_patients(): active_only support", DB_OLD_COUNT_CLINIC_PATIENTS, DB_NEW_COUNT_CLINIC_PATIENTS),
        ("get_escalations_for_clinic() + resolve_escalation_manually()", DB_OLD_MARK_SHOWN, DB_NEW_MARK_SHOWN),
    ])

    print()
    print("[2/2] Patching backend/app.py ...")
    ok_app = patch_file(APP_FILE, [
        ("top imports: pandas", APP_OLD_TOP_IMPORTS, APP_NEW_TOP_IMPORTS),
        ("database import line", APP_OLD_DB_IMPORT, APP_NEW_DB_IMPORT),
        ("clinic dashboard: tabs for Patients / Add patients / Escalations",
         APP_OLD_CLINIC_DASHBOARD, APP_NEW_CLINIC_DASHBOARD),
    ])

    print()
    print("-" * 60)
    if ok_db and ok_app:
        print("DONE. Restart Streamlit (and telegram_bot.py if you use it) to test.")
    else:
        print("Some patches were NOT applied. Nothing was corrupted -")
        print("just copy this whole terminal output and send it to Claude.")
        sys.exit(1)


if __name__ == "__main__":
    main()