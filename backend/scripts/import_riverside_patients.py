# -*- coding: utf-8 -*-
"""
Adds 200 more REAL Synthea patients to the "Riverside General Hospital" clinic
(the one that currently only has Jame Kuhlman in it), using a DIFFERENT random
sample than the 200 patients already imported into Ciro Clinic - so the two
hospitals end up with genuinely separate, non-overlapping patient populations,
which is the whole point of testing the platform's multi-tenant isolation.

Same proven approach as the script that successfully populated Ciro Clinic:
reads the raw multi-file Synthea dataset directly (bypassing the web uploader,
which only accepts a flat per-patient CSV and has a 200MB per-file cap that
medications.parquet already exceeds), filters to living adults with at least
one real active condition or medication, excludes non-clinical "social
determinants of health" entries (employment status, education, etc.), and
writes shell patient records the clinic can later "claim" by registering.

This script finds the clinic BY NAME (not by a hardcoded ID) and finds cara.db
on its own no matter where you run it from - just run:

    python import_riverside_patients.py
"""

import sys
import re
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("[X] pandas is not installed. Run: pip install pandas pyarrow")
    sys.exit(1)

PROJECT_ROOT = Path(r"C:\Users\Markazi.co\Desktop\Thesis")
DATASET_DIR = PROJECT_ROOT / "Patient Dataset"
BACKEND_DIR = PROJECT_ROOT / "backend"

CLINIC_NAME = "Riverside General Hospital"   # looked up by name - no guessed IDs
NUM_PATIENTS = 200
MIN_AGE = 18
RANDOM_SEED = 123     # DIFFERENT from Ciro Clinic's seed (42) -> a different sample
# Jame Kuhlman is already in this clinic (added by import_test_patient.py) - excluded
# so this script can never accidentally duplicate him under a different email.
EXCLUDE_PATIENT_IDS = {"6651990e-6696-1e9f-2068-5fc4cb6dd622"}

sys.path.insert(0, str(BACKEND_DIR))
import database  # noqa: E402
database.init_db()

EXCLUDE_KEYWORDS = [
    "employment", "education", "armed forces", "criminal record", "social contact",
    "social isolation", "labor force", "housing unsatisfactory", "transport",
    "victim of", "risk activity", "refugee", "medication review due",
    "stress (finding)", "lack of access",
]


def is_clinical(description):
    d = str(description).lower()
    return not any(kw in d for kw in EXCLUDE_KEYWORDS)


def clean_name_part(raw):
    # Synthea appends random digits to first/last names to keep them unique
    # (e.g. "Jame996"). Strip a trailing run of digits so names read naturally.
    return re.sub(r"[0-9]+$", "", str(raw)).strip()


def format_allergy_row(row):
    desc = str(row.get("DESCRIPTION", "")).strip()
    severity = row.get("SEVERITY1")
    if pd.notna(severity) and str(severity).strip():
        return f"{desc} ({str(severity).strip().lower()})"
    return desc


def load_parquet(name):
    path = DATASET_DIR / name
    if not path.exists():
        print(f"[X] Could not find: {path}")
        print("    -> Edit DATASET_DIR near the top of this script to match your actual folder.")
        sys.exit(1)
    return pd.read_parquet(path)


def main():
    print("=" * 70)
    print(f"Importing {NUM_PATIENTS} new Synthea patients into '{CLINIC_NAME}'")
    print("=" * 70)

    with database.get_conn() as conn:
        matches = conn.execute(
            "SELECT clinic_id, clinic_name FROM clinics WHERE clinic_name LIKE ?",
            (CLINIC_NAME,),
        ).fetchall()

    if not matches:
        print(f"[X] No clinic found with the name '{CLINIC_NAME}'.")
        print("    Double-check the exact clinic name (case/spelling) and try again.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"[X] Found MORE THAN ONE clinic matching '{CLINIC_NAME}':")
        for r in matches:
            print(f"    - clinic_id={r['clinic_id']}  name={r['clinic_name']!r}")
        print("    Refusing to guess which one - make CLINIC_NAME more specific and re-run.")
        sys.exit(1)

    clinic_id = matches[0]["clinic_id"]
    print(f"Found clinic: '{matches[0]['clinic_name']}' (clinic_id={clinic_id})")

    patients = load_parquet("patients.parquet")
    conditions = load_parquet("conditions.parquet")
    medications = load_parquet("medications.parquet")
    allergies = load_parquet("allergies.parquet")

    print(f"Loaded patients: {len(patients)}, conditions: {len(conditions)}, "
          f"medications: {len(medications)}, allergies: {len(allergies)}")

    # Living adults only
    patients = patients[patients["DEATHDATE"].isna()].copy()
    patients["BIRTHDATE"] = pd.to_datetime(patients["BIRTHDATE"])
    age_years = (pd.Timestamp.now() - patients["BIRTHDATE"]).dt.days / 365.25
    patients = patients[age_years >= MIN_AGE]
    patients = patients[~patients["Id"].isin(EXCLUDE_PATIENT_IDS)]
    print(f"Living adults (18+), excluding patients already in this clinic: {len(patients)}")

    # Only ACTIVE (not resolved) conditions/medications, clinical (not SDOH) ones
    active_conditions = conditions[conditions["STOP"].isna()].copy()
    active_conditions = active_conditions[active_conditions["DESCRIPTION"].apply(is_clinical)]
    active_medications = medications[medications["STOP"].isna()].copy()

    cond_text = (
        active_conditions.groupby("PATIENT")["DESCRIPTION"]
        .apply(lambda s: "; ".join(sorted(set(s))))
    )
    med_text = (
        active_medications.groupby("PATIENT")["DESCRIPTION"]
        .apply(lambda s: "; ".join(sorted(set(s))))
    )
    allergies = allergies.copy()
    allergies["formatted"] = allergies.apply(format_allergy_row, axis=1)
    allergy_text = allergies.groupby("PATIENT")["formatted"].apply(lambda s: "; ".join(sorted(set(s))))

    patients["conditions"] = patients["Id"].map(cond_text).fillna("")
    patients["medications"] = patients["Id"].map(med_text).fillna("")
    patients["allergies"] = patients["Id"].map(allergy_text).fillna("")

    qualifying = patients[(patients["conditions"] != "") | (patients["medications"] != "")]
    print(f"Qualifying patients (>=1 real active condition or medication): {len(qualifying)}")

    if len(qualifying) < NUM_PATIENTS:
        print(f"[X] Only {len(qualifying)} qualifying patients available, "
              f"need {NUM_PATIENTS}. Lower NUM_PATIENTS near the top of this script and re-run.")
        sys.exit(1)

    sample = qualifying.sample(n=NUM_PATIENTS, random_state=RANDOM_SEED)

    used_emails = set()
    added, skipped = 0, []
    for _, row in sample.iterrows():
        first = clean_name_part(row["FIRST"])
        last = clean_name_part(row["LAST"])
        full_name = f"{first} {last}".strip()
        base_email = f"{first}.{last}".lower().replace(" ", "")
        email = f"{base_email}@test.com"
        suffix = 2
        while email in used_emails:
            email = f"{base_email}{suffix}@test.com"
            suffix += 1
        used_emails.add(email)

        ok, err = database.add_patient_shell(
            clinic_id, full_name, email,
            row["conditions"], row["medications"], row["allergies"],
        )
        if ok:
            added += 1
        else:
            skipped.append(f"{full_name} ({email}): {err}")

    print("-" * 70)
    print(f"Added: {added}, Skipped (already existed): {len(skipped)}")
    if skipped:
        print("Skipped patients (first 20):")
        for s in skipped[:20]:
            print(f"  - {s}")
    print(f"\n'{CLINIC_NAME}' (clinic_id={clinic_id}) now has {added} new patients "
          f"available to claim, on top of whoever was already there.")


if __name__ == "__main__":
    main()
