"""A single synthetic clinical/legal document exercising the hard layout cases.

All values are invented — **no real PII**. The document deliberately includes:
  * inline labeled fields (``Patient: ...``, ``SSN: ...``),
  * a multi-field line (name, DOB, MRN together),
  * a stacked-label block (emergency contact),
  * a small table (lab results),
  * a signature block,
  * running headers/footers,
  * a prose paragraph with embedded PII **and** protected clinical numbers — a dose (``500 mg``)
    and a viral load (``100000 copies/mL``) that MUST survive (unit-adjacency carve-out).

``FIXTURE_PII`` is the ground-truth set of values that must NOT appear in the outbound payload.
``FIXTURE_PRESERVED`` is the set of clinically load-bearing values that MUST survive.
"""

from __future__ import annotations

FIXTURE_DOC_ID = "note-2041-A"

FIXTURE_TEXT = """\
MERCY GENERAL HOSPITAL — CONFIDENTIAL PATIENT RECORD
================================================================

Patient: Jonathan Reyes    DOB: 03/15/1985    MRN: 4457812
SSN: 482-19-7734
Phone: (415) 555-0132       Email: jreyes@example.com
Address: 1420 Alderwood Street, San Jose, CA 95112
Insurance ID: BCX882471190

Emergency Contact:
  Name: Maria Reyes
  Phone: 415-555-0177
  Relationship: Spouse

HISTORY OF PRESENT ILLNESS
Mr. Reyes is a 62-year-old Han Chinese male presenting for HIV follow-up. Jonathan reports
good adherence. He was started on therapy in 2019. Reyes tolerates the regimen at 500 mg
twice daily with no adverse effects reported at this visit.

LABORATORY RESULTS
  Test                 Result              Reference
  HIV-1 RNA            100000 copies/mL    < 20
  CD4 count            410 cells/uL        500-1500
  Hemoglobin           13.9 g/dL           13.5-17.5

ASSESSMENT & PLAN
Continue current antiretroviral therapy. Recheck viral load in 3 months. Patient counseled.
Attending Physician: Dr. Priya Nair    DEA: BN4471203
Account No: ACCT-99120-7

Electronically signed by:
  Priya Nair, MD
  Mercy General Hospital, Infectious Disease

MERCY GENERAL HOSPITAL — page 1 of 1 — record note-2041-A
"""

#: Values that must NEVER appear in the outbound payload (exact strings).
FIXTURE_PII: dict[str, str] = {
    "patient_name": "Jonathan Reyes",
    "patient_first": "Jonathan",
    "patient_last": "Reyes",
    "contact_name": "Maria Reyes",
    "provider_name": "Priya Nair",
    "ssn": "482-19-7734",
    "dob": "03/15/1985",
    "mrn": "4457812",
    "phone": "(415) 555-0132",
    "contact_phone": "415-555-0177",
    "email": "jreyes@example.com",
    "street": "1420 Alderwood Street",
    "insurance": "BCX882471190",
    "dea": "BN4471203",
    "account": "ACCT-99120-7",
}

#: Clinically load-bearing values that MUST survive obfuscation (Safe Harbor permits; the model
#: needs them to reason). Dose and viral load are the unit-adjacency carve-out in action.
FIXTURE_PRESERVED: dict[str, str] = {
    "dose": "500 mg",
    "viral_load": "100000 copies/mL",
    "cd4": "410 cells/uL",
    "hemoglobin": "13.9 g/dL",
    "ethnicity": "Han Chinese",
    "sex": "male",
    "age": "62",
}
