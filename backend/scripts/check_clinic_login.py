# -*- coding: utf-8 -*-
"""
Temporary, read-only diagnostic: lists every clinic account in cara.db,
so you can see the REGISTERED LOGIN EMAIL and CONTACT NAME for Riverside
General Hospital (or any clinic) with real evidence instead of guessing.

IMPORTANT - what this script can and cannot do:
Passwords are stored as bcrypt hashes, which are mathematically ONE-WAY.
There is no way for this script, or anyone, to reverse a hash back into
the original password. So this script can tell you the email and contact
name, but NOT the password itself. Your two options after running this:

  1) Recognize the email printed below and recall/guess the password, OR
  2) Use "Forgot password?" on the CLINIC login screen (this is a
     different form than the patient one) - it asks for the exact email
     and exact contact person's name, then lets you set a brand-new
     password. Whatever this script prints for "contact_name" must be
     typed there EXACTLY, including capitalization and spacing.

Run from the backend folder:
    C:\\Users\\Markazi.co\\Desktop\\Thesis\\backend> python check_clinic_login.py

Safe to delete once we're done troubleshooting - it's not part of the app.
"""

import database as db

print("=" * 70)
print(f"Using database file: {db.DB_PATH.resolve()}")
print("=" * 70)

with db.get_conn() as conn:
    rows = conn.execute(
        "SELECT clinic_id, clinic_name, clinic_type, contact_name, phone, "
        "address, email, password_hash FROM clinics ORDER BY clinic_id"
    ).fetchall()

if not rows:
    print("\nNo clinics at all exist in this database file.")
    print("That would mean this script is reading a DIFFERENT cara.db than the")
    print("one Streamlit actually uses. Tell me the exact 'cd' + 'streamlit run'")
    print("commands you use to start the app so we can check for a mismatch.")
else:
    print(f"\nAll {len(rows)} clinic account(s) in the database:\n")
    for r in rows:
        r = dict(r)
        has_password = "yes" if r["password_hash"] else "NO PASSWORD SET (broken account)"
        marker = "  <<<< THIS ONE" if r["clinic_id"] == 2 else ""
        print(f"  clinic_id={r['clinic_id']}  name={r['clinic_name']!r}{marker}")
        print(f"      type:              {r['clinic_type']}")
        print(f"      contact_name:      {r['contact_name']!r}")
        print(f"      phone:             {r['phone']}")
        print(f"      address:           {r['address']}")
        print(f"      login email:       {r['email']!r}")
        print(f"      password is set:   {has_password}")
        print()

    target = next((dict(r) for r in rows if dict(r)["clinic_id"] == 2), None)
    print("-" * 70)
    if target:
        print("[FOCUS] Clinic ID 2 - this should be Riverside General Hospital:")
        print(f"  Registered name:   {target['clinic_name']!r}")
        print(f"  Login email:       {target['email']}")
        print(f"  Contact name:      {target['contact_name']!r}   <- must match EXACTLY")
        print()
        print("Next steps:")
        print("  1. Try logging in on the CLINIC tab with the email above, in case the")
        print("     password comes back to you once you see the email.")
        print("  2. If not, open the clinic login page, click 'Forgot password?', and")
        print("     type the email and the contact name EXACTLY as printed above (same")
        print("     capitalization and spacing), then set a brand-new password.")
    else:
        print("[X] No clinic with clinic_id=2 was found. Check the full list above -")
        print("    Riverside General Hospital may be registered under a different ID.")
