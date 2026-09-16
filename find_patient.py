import pandas as pd

DATA_DIR = r"C:\Users\Markazi.co\Desktop\Thesis\Patient Dataset"

patients = pd.read_parquet(DATA_DIR + r"\patients.parquet")
conditions = pd.read_parquet(DATA_DIR + r"\conditions.parquet")
medications = pd.read_parquet(DATA_DIR + r"\medications.parquet")
allergies = pd.read_parquet(DATA_DIR + r"\allergies.parquet")

# Find patients on a blood thinner (interesting drug-interaction test case)
blood_thinners = medications[medications["DESCRIPTION"].str.contains(
    "warfarin|Warfarin|Coumadin", case=False, na=False)]
candidate_ids = blood_thinners["PATIENT"].unique()

# Prefer one who ALSO has a heart-related condition (most realistic high-risk case)
cardiac = conditions[
    conditions["PATIENT"].isin(candidate_ids) &
    conditions["DESCRIPTION"].str.contains("heart|cardiac|atrial|coronary", case=False, na=False)
]

chosen_id = cardiac.iloc[0]["PATIENT"] if len(cardiac) > 0 else candidate_ids[0]

p = patients[patients["Id"] == chosen_id].iloc[0]
p_conditions = conditions[conditions["PATIENT"] == chosen_id]["DESCRIPTION"].unique()
p_meds = medications[medications["PATIENT"] == chosen_id]["DESCRIPTION"].unique()
p_allergies = allergies[allergies["PATIENT"] == chosen_id]["DESCRIPTION"].unique()

print("Patient ID:", chosen_id)
print("Name:", p["FIRST"], p["LAST"])
print("Birthdate:", p["BIRTHDATE"])
print("Gender:", p["GENDER"])
print("\nConditions:")
for c in p_conditions:
    print(" -", c)
print("\nMedications:")
for m in p_meds:
    print(" -", m)
print("\nAllergies:")
for a in p_allergies:
    print(" -", a)
if len(p_allergies) == 0:
    print(" (none)")