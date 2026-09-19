# -*- coding: utf-8 -*-
"""
Temporary, read-only diagnostic: lists every escalation currently in cara.db,
so we can see with real evidence whether a question actually got saved, and
whether telegram_bot.py has already tried (and succeeded/failed) to send it.

Run from the backend folder:
    C:\\Users\\Markazi.co\\Desktop\\Thesis\\backend> python check_escalations.py

Safe to delete once we're done troubleshooting - it's not part of the app.
"""

import database as db

print("=" * 70)
print(f"Using database file: {db.DB_PATH.resolve()}")
print("=" * 70)

with db.get_conn() as conn:
    rows = conn.execute(
        "SELECT e.escalation_id, e.status, e.telegram_message_id, e.created_at, "
        "e.question, p.full_name "
        "FROM escalations e JOIN patients p ON e.patient_id = p.patient_id "
        "ORDER BY e.escalation_id DESC LIMIT 10"
    ).fetchall()

if not rows:
    print("\nNo escalations at all exist in this database file.")
    print("This would mean the app that created the escalation is NOT writing to")
    print("this same cara.db file - a working-directory mismatch, same family of")
    print("bug as before. Tell me exactly how you currently start Streamlit (the")
    print("full 'cd' + 'streamlit run' commands you used) so we can check it.")
else:
    print(f"\nMost recent {len(rows)} escalation(s) (newest first):\n")
    for r in rows:
        r = dict(r)
        print(f"  #{r['escalation_id']}  patient={r['full_name']!r}  status={r['status']}  "
              f"telegram_message_id={r['telegram_message_id']}  created_at={r['created_at']}")
        print(f"       question: {r['question'][:80]}")
    print()
    top = dict(rows[0])
    if top["status"] == "pending" and top["telegram_message_id"] is None:
        print("[DIAGNOSIS] This escalation IS in the database and IS pending, but has")
        print("NOT been picked up by telegram_bot.py yet (telegram_message_id is empty).")
        print("Either telegram_bot.py hasn't completed its next polling cycle yet")
        print("(it can take up to ~13 seconds), or it's reading a DIFFERENT cara.db")
        print("file than this one. Wait ~20 seconds, watch the telegram_bot.py")
        print("terminal window, and re-run this script to see if telegram_message_id")
        print("changes from None to a real number.")
    elif top["status"] == "pending" and top["telegram_message_id"] is not None:
        print("[DIAGNOSIS] telegram_bot.py DID already send this to Telegram")
        print(f"(telegram_message_id = {top['telegram_message_id']}) and it's still")
        print("waiting for the doctor's reply. Check the Telegram chat the bot posts")
        print("to - the message may have arrived but simply been missed/scrolled past.")
    else:
        print(f"[DIAGNOSIS] This escalation's status is '{top['status']}', not pending -")
        print("it may already have been resolved.")
