import sys
import random
import re
from pathlib import Path
from datetime import datetime

import pandas as pd

PROJECT_ROOT = Path(r"C:\Users\Markazi.co\Desktop\Thesis")
DATASET_DIR = PROJECT_ROOT / "Patient Dataset"
BACKEND_DIR = PROJECT_ROOT / "backend"

CLINIC_ID = 1
NUM_PATIENTS = 200
MIN_AGE = 18
RANDOM_SEED = 42

sys.path.insert(0, str(BACKEND_DIR))
import database

database.init_db()

EXCLUDE_KEYWORDS = [
    "employment",
    "education",
    "armed forces",
    "criminal record",
    "social contact",
    "social isolation",
    "labor force",
    "housing unsatisfactory",
    "transport",
    "victim of",
    "risk activity",
    "refugee",
    "medication review due",
    "stress (finding)",
    "lack of access",
]


def is_real_condition(description):
    text = description.lower()
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return False
    return True


def clean_name_part(raw):
    return re.sub(r"[0-9]+$", "", raw).strip()


def main():
    print("Loading patients.parquet ...")
    patients = pd.read_parquet(DATASET_DIR / "patients.parquet")
    print("Loading conditions.parquet (this may take a bit, file is ~111MB) ...")
    conditions = pd.read_parquet(DATASET_DIR / "conditions.parquet")
    print("Loading medications.parquet (this may take a bit, file is ~252MB) ...")
    medications = pd.read_parquet(DATASET_DIR / "medications.parquet")
    print("Loading allergies.parquet ...")
    allergies = pd.read_parquet(DATASET_DIR / "allergies.parquet")

    print("Filtering alive adult patients ...")
    alive = patients[patients["DEATHDATE"].isna()].copy()
    alive["BIRTHDATE"] = pd.to_datetime(alive["BIRTHDATE"])
    today = pd.Timestamp(datetime.now().date())
    alive["AGE"] = ((today - alive["BIRTHDATE"]).dt.days / 365.25).astype(int)
    adults = alive[alive["AGE"] >= MIN_AGE].copy()
    print("Adult alive patients available:", len(adults))

    print("Filtering to active condition and medication records ...")
    active_conditions = conditions[conditions["STOP"].isna()].copy()
    active_conditions = active_conditions[active_conditions["DESCRIPTION"].apply(is_real_condition)]
    active_medications = medications[medications["STOP"].isna()].copy()

    print("Grouping conditions by patient ...")
    cond_by_patient = active_conditions.groupby("PATIENT")["DESCRIPTION"].apply(
        lambda s: "; ".join(sorted(set(s)))
    )
    print("Grouping medications by patient ...")
    med_by_patient = active_medications.groupby("PATIENT")["DESCRIPTION"].apply(
        lambda s: "; ".join(sorted(set(s)))
    )

    print("Grouping allergies by patient ...")

    def format_allergy_row(sub):
        parts = []
        for _, r in sub.iterrows():
            desc = r["DESCRIPTION"]
            sev = r.get("SEVERITY1")
            if isinstance(sev, str) and sev:
                parts.append(desc + " (" + sev.lower() + ")")
            else:
                parts.append(desc)
        return "; ".join(sorted(set(parts)))

    allergy_by_patient = allergies.groupby("PATIENT").apply(format_allergy_row)

    adults["CONDITIONS_TEXT"] = adults["Id"].map(cond_by_patient).fillna("")
    adults["MEDICATIONS_TEXT"] = adults["Id"].map(med_by_patient).fillna("")
    adults["ALLERGIES_TEXT"] = adults["Id"].map(allergy_by_patient).fillna("")

    qualifying = adults[(adults["CONDITIONS_TEXT"] != "") | (adults["MEDICATIONS_TEXT"] != "")].copy()
    print("Qualifying patients (have at least one real condition or medication):", len(qualifying))

    random.seed(RANDOM_SEED)
    ids = list(qualifying["Id"])
    if len(ids) > NUM_PATIENTS:
        chosen_ids = set(random.sample(ids, NUM_PATIENTS))
    else:
        chosen_ids = set(ids)
        print("WARNING: only", len(ids), "qualifying patients found, importing all of them.")

    chosen = qualifying[qualifying["Id"].isin(chosen_ids)].copy()
    print("Selected", len(chosen), "patients to import into clinic_id =", CLINIC_ID)

    used_emails = set()
    added = 0
    skipped = 0
    log_lines = []
    log_lines.append("full_name|email|num_conditions|num_medications|num_allergies|status")

    for _, row in chosen.iterrows():
        first = clean_name_part(str(row["FIRST"]))
        last = clean_name_part(str(row["LAST"]))
        full_name = (first + " " + last).strip()
        base_email = (first + "." + last).lower().replace(" ", "")
        email = base_email + "@test.com"
        suffix = 2
        while email in used_emails:
            email = base_email + str(suffix) + "@test.com"
            suffix += 1
        used_emails.add(email)

        cond_text = row["CONDITIONS_TEXT"]
        med_text = row["MEDICATIONS_TEXT"]
        alg_text = row["ALLERGIES_TEXT"]

        ok, err = database.add_patient_shell(CLINIC_ID, full_name, email, cond_text, med_text, alg_text)
        n_cond = 0 if not cond_text else len(cond_text.split("; "))
        n_med = 0 if not med_text else len(med_text.split("; "))
        n_alg = 0 if not alg_text else len(alg_text.split("; "))

        if ok:
            added += 1
            status = "added"
        else:
            skipped += 1
            status = "skipped: " + str(err)

        log_lines.append(
            full_name + "|" + email + "|" + str(n_cond) + "|" + str(n_med) + "|" + str(n_alg) + "|" + status
        )

    log_path = PROJECT_ROOT / "import_200_patients_log.csv"
    with open(log_path, "w", encoding="utf-8") as f:
        for line in log_lines:
            f.write(line)
            f.write(chr(10))

    print("----")
    print("DONE.")
    print("Added:", added)
    print("Skipped (already existed):", skipped)
    print("Full log saved to:", log_path)


if __name__ == "__main__":
    main()
