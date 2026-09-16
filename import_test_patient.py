import sys
import pandas as pd

sys.path.insert(0, "backend")
from database import init_db, register_clinic, register_patient, login_clinic

DATA_DIR = r"C:\Users\Markazi.co\Desktop\Thesis\Patient Dataset"

NON_CLINICAL_KEYWORDS = [
    "employment", "criminal record", "violence in the environment",
    "social contact", "social isolation", "medication review due",
    "certificate of high school", "stress (finding)",
]


def is_clinical(description):
    d = description.lower()
    return not any(kw in d for kw in NON_CLINICAL_KEYWORDS)


def clean_name(raw_name):
    return "".join(ch for ch in raw_name if not ch.isdigit())


init_db()

patients = pd.read_parquet(DATA_DIR + r"\patients.parquet")
conditions = pd.read_parquet(DATA_DIR + r"\conditions.parquet")
medications = pd.read_parquet(DATA_DIR + r"\medications.parquet")
allergies = pd.read_parquet(DATA_DIR + r"\allergies.parquet")

PATIENT_ID = "6651990e-6696-1e9f-2068-5fc4cb6dd622"

p = patients[patients["Id"] == PATIENT_ID].iloc[0]
p_conditions = [c for c in conditions[conditions["PATIENT"] == PATIENT_ID]["DESCRIPTION"].unique() if is_clinical(c)]
p_meds = medications[medications["PATIENT"] == PATIENT_ID]["DESCRIPTION"].unique().tolist()
p_allergies = allergies[allergies["PATIENT"] == PATIENT_ID]["DESCRIPTION"].unique().tolist()

full_name = clean_name(f"{p['FIRST']} {p['LAST']}")

# 1. Create (or reuse) a test hospital
ok, err = register_clinic(
    clinic_name="Riverside General Hospital",
    clinic_type="Hospital",
    contact_name="Dr. Alan Reyes",
    phone="555-0100",
    address="100 Riverside Ave, Austin, TX",
    email="admin@riverside-test.com",
    password="hospital123",
)
print("Clinic created:", ok, err or "")

clinic = login_clinic("admin@riverside-test.com", "hospital123")
print("Clinic ID:", clinic["clinic_id"])

# 2. Register the real Synthea patient under this hospital
ok, err = register_patient(
    clinic_id=clinic["clinic_id"],
    full_name=full_name,
    email="jame.kuhlman@test.com",
    password="patient123",
    conditions="; ".join(p_conditions),
    medications="; ".join(p_meds),
    allergies="; ".join(p_allergies) if p_allergies else "None known",
)
print("Patient created:", ok, err or "")
print("\nLogin with: jame.kuhlman@test.com / patient123")
print(f"Name: {full_name}")
print(f"Conditions ({len(p_conditions)}):", "; ".join(p_conditions))
print(f"Medications ({len(p_meds)}):", "; ".join(p_meds))