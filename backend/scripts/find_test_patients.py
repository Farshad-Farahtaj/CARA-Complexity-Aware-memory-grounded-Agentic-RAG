# -*- coding: utf-8 -*-
"""
Temporary, read-only diagnostic: scans the 200 REAL Synthea patients in
Riverside General Hospital (clinic_id=2) and flags good candidates for
testing CARA's escalation logic on real-world data - patients whose
condition + medication + allergy combination creates a genuine clinical
safety question, the same way Patricia Alvarez (warfarin + tooth
extraction) did for the hand-crafted dental clinic.

It checks five well-known dangerous/complex combinations:
  1) Anticoagulant (bleeding risk) - warfarin, apixaban, rivaroxaban, etc.
  2) NSAID + chronic kidney disease (nephrotoxicity risk)
  3) Penicillin allergy + an infection-type condition (needs an alternative
     antibiotic, not a workaround with penicillin)
  4) Beta-blocker + asthma/COPD (beta-blockers can worsen airway disease)
  5) Opioid + COPD/sleep apnea (respiratory depression risk)

This does NOT change anything - it only reads and prints. Run from the
backend folder:
    C:\\Users\\Markazi.co\\Desktop\\Thesis\\backend> python find_test_patients.py

Safe to delete once we're done picking test patients.
"""

import database as db

RIVERSIDE_CLINIC_ID = 2
MAX_PER_CATEGORY = 4

ANTICOAGULANTS = ["warfarin", "apixaban", "rivaroxaban", "clopidogrel", "dabigatran", "heparin"]
NSAIDS = ["ibuprofen", "naproxen", "diclofenac", "meloxicam", "celecoxib", "ketorolac"]
CKD_TERMS = ["chronic kidney disease", "renal failure", "kidney failure"]
INFECTION_TERMS = ["sinusitis", "pneumonia", "cellulitis", "abscess", "bronchitis",
                    "otitis media", "urinary tract infection", "strep throat", "pharyngitis"]
BETA_BLOCKERS = ["metoprolol", "atenolol", "propranolol", "carvedilol", "bisoprolol"]
AIRWAY_TERMS = ["asthma", "chronic obstructive pulmonary disease", "copd"]
OPIOIDS = ["oxycodone", "hydrocodone", "morphine", "fentanyl", "tramadol", "codeine"]
RESP_RISK_TERMS = ["chronic obstructive pulmonary disease", "copd", "sleep apnea"]


def has_any(text, terms):
    text = (text or "").lower()
    return any(t in text for t in terms)


def print_match(category, p):
    print(f"  - {p['full_name']}  <{p['email']}>")
    print(f"      conditions:  {p['conditions']}")
    print(f"      medications: {p['medications']}")
    print(f"      allergies:   {p['allergies']}")
    print()


with db.get_conn() as conn:
    patients = conn.execute(
        "SELECT full_name, email, conditions, medications, allergies "
        "FROM patients WHERE clinic_id = ?", (RIVERSIDE_CLINIC_ID,)
    ).fetchall()

patients = [dict(p) for p in patients]
print(f"Scanning {len(patients)} patients in Riverside General Hospital (clinic_id={RIVERSIDE_CLINIC_ID})...")
print("=" * 70)

categories = [
    ("1) ANTICOAGULANT (bleeding risk)",
     lambda p: has_any(p["medications"], ANTICOAGULANTS)),
    ("2) NSAID + CHRONIC KIDNEY DISEASE (nephrotoxicity)",
     lambda p: has_any(p["medications"], NSAIDS) and has_any(p["conditions"], CKD_TERMS)),
    ("3) PENICILLIN ALLERGY + INFECTION (needs alternative antibiotic)",
     lambda p: "penicillin" in (p["allergies"] or "").lower() and has_any(p["conditions"], INFECTION_TERMS)),
    ("4) BETA-BLOCKER + ASTHMA/COPD (can worsen airway disease)",
     lambda p: has_any(p["medications"], BETA_BLOCKERS) and has_any(p["conditions"], AIRWAY_TERMS)),
    ("5) OPIOID + COPD/SLEEP APNEA (respiratory depression risk)",
     lambda p: has_any(p["medications"], OPIOIDS) and has_any(p["conditions"], RESP_RISK_TERMS)),
]

any_found = False
for title, predicate in categories:
    matches = [p for p in patients if predicate(p)]
    print(f"\n{title}  ->  {len(matches)} match(es)")
    print("-" * 70)
    if matches:
        any_found = True
        for p in matches[:MAX_PER_CATEGORY]:
            print_match(title, p)
    else:
        print("  (none found in this batch of 200)")

if not any_found:
    print("\nNo matches in any category. This can happen since the 200 patients were")
    print("randomly sampled - tell me and we'll widen the search or pick a different angle.")
else:
    print("=" * 70)
    print("Pick a name from above and tell me which one - I'll write a realistic,")
    print("English-language test question for that specific patient's situation.")
