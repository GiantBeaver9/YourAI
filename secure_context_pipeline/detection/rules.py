"""Standard detection rules as data (HIPAA Safe Harbor set) + validators + clinical lexicons.

Adding an entity type = a row here (extensibility NFR). These compile to native regex in the
rule engine and to Presidio recognizers in the Presidio backend.
"""

from __future__ import annotations

from ..entities import EntityType
from .base import RuleSpec


def _luhn_ok(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 13:
        return False
    total, alt = 0, False
    for d in reversed(digits):
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def _ssn_ok(s: str) -> bool:
    d = [c for c in s if c.isdigit()]
    if len(d) != 9:
        return False
    area, group, serial = "".join(d[:3]), "".join(d[3:5]), "".join(d[5:])
    if area in ("000", "666") or area[0] == "9":
        return False
    return group != "00" and serial != "0000"


# --- structured / formatted identifiers (deterministic, high precision) ---
STANDARD_RULES: list[RuleSpec] = [
    RuleSpec(EntityType.SSN, r"\b\d{3}-\d{2}-\d{4}\b", validator=_ssn_ok, confidence=0.95, name="ssn-dashed"),
    RuleSpec(EntityType.EMAIL, r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", confidence=0.98, name="email"),
    RuleSpec(EntityType.PHONE, r"\b\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", confidence=0.9, name="phone"),
    RuleSpec(EntityType.CREDIT_CARD, r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b", validator=_luhn_ok, confidence=0.9, name="cc"),
    RuleSpec(EntityType.IP, r"\b\d{1,3}(?:\.\d{1,3}){3}\b", confidence=0.9, name="ip"),
    RuleSpec(EntityType.URL, r"\bhttps?://\S+\b", confidence=0.95, name="url"),
    RuleSpec(EntityType.ZIP, r"\b\d{5}(?:-\d{4})?\b", context=("zip", "postal"), confidence=0.6, name="zip"),
    # dates (numeric + long-form)
    RuleSpec(EntityType.DATE, r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", confidence=0.85, name="date-numeric"),
    RuleSpec(
        EntityType.DATE,
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        confidence=0.85, name="date-longform",
    ),
]

# --- label-driven (no universal format -> the label types the value) ---
# handled specially by the rule engine: (label regex, entity type, confidence)
LABEL_RULES: list[tuple[str, EntityType, float]] = [
    (r"(?i)\b(?:MRN|medical record(?:\s*(?:number|no|#))?)\s*[:#]?\s*", EntityType.MRN, 0.9),
    (r"(?i)\b(?:account|acct)\s*(?:number|no|#)?\s*[:#]?\s*", EntityType.ACCOUNT, 0.85),
    (r"(?i)\b(?:member|policy|insurance)\s*(?:id|number|no|#)?\s*[:#]?\s*", EntityType.INSURANCE_ID, 0.85),
    (r"(?i)\b(?:patient\s+name|name|guarantor)\s*[:#]\s*", EntityType.NAME, 0.9),
    (r"(?i)\b(?:date\s+of\s+birth|DOB|birth\s*date)\s*[:#]?\s*", EntityType.DOB, 0.95),
]

# --- clinical quasi-identifiers: detected so the policy can PRESERVE them explicitly ---
ETHNICITY_TERMS = (
    "han chinese", "chinese", "japanese", "korean", "vietnamese", "filipino",
    "caucasian", "white", "african american", "black", "hispanic", "latino", "latina",
    "asian", "south asian", "native american", "pacific islander", "ashkenazi",
)
SEX_TERMS = ("male", "female", "man", "woman", "intersex", "nonbinary")

# clinical unit tokens that RESCUE a 5+ digit run from the magnitude scrub (lab values)
CLINICAL_UNITS = (
    "copies/ml", "copies/ml", "iu/ml", "/ul", "/µl", "cells/ul", "x10^9", "x10e9",
    "mg", "mcg", "ml", "mmhg", "bpm", "mg/dl", "mmol/l", "k/ul", "u/l",
)
