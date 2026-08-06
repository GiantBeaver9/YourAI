"""Detection rules as **data** (detection-rules-engine.md).

Every rule — a Safe Harbor identifier, a structural field anchor, a per-tenant custom format —
has the same shape: an optional ``preceding`` anchor, a value ``regex``, an optional
``succeeding`` boundary, an ``entity_type`` and an ``action``. The same rows drive both
substrates: they compile into Presidio ``PatternRecognizer``\\s (context words = the anchor),
and, when Presidio is absent, into native ``re`` patterns. Adding an entity type is a **row**,
not a code change — the extensibility NFR at its strongest.

Standard rules are the vetted built-ins here (Safe Harbor set + formats); custom rules are the
same dataclass authored per deployment. The runtime rule-authoring surface (RE2 intake
validation + example/preview panel) is designed-not-built — see the design doc — but the rule
*shape* it would populate is exactly this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..entities import EntityType

# Value captured in a named group so anchored rules return the *value* span, not the label.
_VAL = "scp_val"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    entity_type: EntityType
    regex: str | None = None            # the value pattern (defaults to a line-bounded value)
    preceding: str | None = None        # label/anchor BEFORE the value (structural rule)
    succeeding: str | None = None       # boundary AFTER the value
    action: str = "scrub"               # scrub | obfuscate — rule-level intent; policy routes strategy
    enabled: bool = True
    source: str = "standard"            # standard | custom
    priority: int = 50
    confidence: float = 0.85
    ignore_case: bool = True

    def compiled(self) -> re.Pattern:
        """Compile to a single pattern with the value in group :data:`_VAL`.

        Anchored rules fold ``preceding``/``succeeding`` into lookaround-free concatenation so
        the reported span covers only the value (the anchor stays outside the group). Anchors
        (labels) are ALWAYS matched case-insensitively via a scoped ``(?i:...)`` group; the
        value's case sensitivity is governed by ``ignore_case`` — so a name rule can require
        capitalization on the value while still matching a lower/upper-cased "patient:" label."""
        value = self.regex or r"[^\n,;]+?"
        parts = []
        if self.preceding:
            parts.append(f"(?i:{self.preceding})")
        parts.append(f"(?P<{_VAL}>{value})")
        if self.succeeding:
            parts.append(f"(?i:(?={self.succeeding}))")  # boundary as lookahead — not consumed
        flags = re.IGNORECASE if self.ignore_case else 0
        return re.compile("".join(parts), flags)

    @property
    def value_group(self) -> str:
        return _VAL


# --- vocabularies for the small closed-set recognizers ------------------------------------
SEX_TERMS = ["male", "female", "man", "woman", "transgender", "nonbinary", "non-binary"]
ETHNICITY_TERMS = [
    "White", "Black", "African American", "Asian", "Han Chinese", "Hispanic", "Latino",
    "Latina", "Native American", "Pacific Islander", "Ashkenazi", "Caucasian", "Korean",
    "Japanese", "Vietnamese", "Filipino", "South Asian", "Middle Eastern",
]
# Honorifics that prefix a name — used to catch "Dr. Smith" / "Mr. Jones" without an anchor.
HONORIFICS = ["Dr", "Mr", "Mrs", "Ms", "Miss", "Prof", "Rev", "Hon"]

# US state abbreviations — anchor ZIPs and cities (Safe Harbor #2/#17 geography).
US_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()

# Single-word "names" that are really field labels / section words — dropped when a detector
# (esp. a statistical NER) mislabels them PERSON. Only filters SINGLE-token matches, so real
# surnames ("Reyes") are never affected.
NAME_LABEL_STOP = {
    "email", "phone", "name", "address", "patient", "relationship", "spouse", "account",
    "insurance", "member", "policy", "provider", "physician", "attending", "contact",
    "emergency", "date", "birth", "sex", "gender", "age", "result", "reference", "test",
    "assessment", "plan", "history", "present", "illness", "laboratory", "results", "hospital",
    "record", "confidential", "page", "note", "signed", "electronically", "continue",
    "recheck", "reviewed", "relationship", "subscriber", "docket", "case", "referring",
    # honorifics — a bare "Dr"/"Mr" is a title, never a name on its own
    "dr", "mr", "mrs", "ms", "miss", "prof", "rev", "hon",
}

# Clinical unit tokens — the magnitude rule keeps a long digit run adjacent to one of these
# (viral loads, cell counts) instead of scrubbing a real lab value.
CLINICAL_UNITS = [
    "mg", "mcg", "µg", "ug", "ng", "g", "kg", "mL", "ml", "L", "dL",
    "copies/mL", "copies/ml", "cells/µL", "cells/uL", "cells/mm3", "/mL", "/uL", "/µL",
    "mmHg", "bpm", "mmol/L", "mg/dL", "mEq/L", "IU", "units", "%", "cm", "mm", "kcal",
]

# A name token is a capitalized word (with internal apostrophes/hyphens) OR an initial ("J.").
# Crucially a WORD token carries no trailing dot, so a sentence-ending period is NOT consumed —
# that stops a name value from bleeding across "Smith. Contact:" into the next field's label.
# Tokens join on a SINGLE space, so a name also can't cross a line break or the multi-space
# column gap between fields on a shared line ("Name    DOB:").
_NAME_TOKEN = r"(?:[A-Z]\.|[A-Z][A-Za-z'’-]+)"
_NAME_VALUE = _NAME_TOKEN + r"(?:[ ]" + _NAME_TOKEN + r"){0,3}"
_DATE_VALUE = (
    r"(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|\d{4}-\d{1,2}-\d{1,2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})"
)


def _ci_group(terms: list[str]) -> str:
    # Longest-first so "African American" wins over "American"; word-bounded.
    ordered = sorted(terms, key=len, reverse=True)
    return r"\b(?:" + "|".join(re.escape(t) for t in ordered) + r")\b"


#: The vetted standard rule set — the Safe Harbor identifiers plus structural/format rules.
STANDARD_RULES: list[Rule] = [
    # --- strongly-formatted identifiers (unanchored, high confidence) ---
    Rule("ssn", "SSN", EntityType.SSN, regex=r"\b\d{3}-\d{2}-\d{4}\b",
         priority=90, confidence=0.97),
    Rule("ssn_labeled", "SSN (labeled)", EntityType.SSN,
         preceding=r"(?:SSN|Social Security(?:\s+(?:No|Number|#))?)\s*[:#]?\s*",
         regex=r"\d{3}-?\d{2}-?\d{4}", priority=95, confidence=0.98),
    Rule("email", "Email", EntityType.EMAIL,
         regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
         priority=90, confidence=0.97),
    Rule("phone", "Phone", EntityType.PHONE,
         regex=r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
         priority=85, confidence=0.9),
    Rule("credit_card", "Credit card", EntityType.CREDIT_CARD,
         regex=r"\b(?:\d[ -]?){15,16}\b", priority=88, confidence=0.9),
    Rule("ipv4", "IPv4", EntityType.IP,
         regex=r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
         priority=80, confidence=0.9),
    Rule("url", "URL", EntityType.URL,
         regex=r"\bhttps?://[^\s,)]+", priority=80, confidence=0.9),

    # --- structural / anchored fields (label carries the meaning) ---
    Rule("mrn", "MRN", EntityType.MRN,
         preceding=r"(?:MRN|Medical Record(?:\s+(?:No|Number|#))?)\s*[:#]?\s*",
         regex=r"[A-Z0-9-]{5,12}", priority=92, confidence=0.95),
    Rule("dea", "DEA number", EntityType.GENERIC_ID,
         preceding=r"DEA(?:\s*(?:No|Number|#))?\s*[:#]?\s*",
         regex=r"[A-Z]{2}\d{7}", priority=90, confidence=0.95),
    Rule("npi", "NPI", EntityType.GENERIC_ID,
         preceding=r"NPI\s*[:#]?\s*", regex=r"\d{10}", priority=88, confidence=0.95),
    Rule("account", "Account number", EntityType.ACCOUNT,
         preceding=r"(?:Account|Acct)(?:\s*(?:No|Number|#))?\s*[:#]?\s*",
         regex=r"[A-Z0-9-]{4,20}", priority=85, confidence=0.9),
    Rule("insurance", "Insurance/Member ID", EntityType.INSURANCE_ID,
         preceding=r"(?:Member(?:\s+ID)?|Policy(?:\s+(?:No|Number|#))?|Insurance(?:\s+ID)?|Subscriber(?:\s+ID)?)\s*[:#]?\s*",
         regex=r"[A-Z0-9-]{6,20}", priority=85, confidence=0.9),
    Rule("case_no", "Legal case number", EntityType.LEGAL_CASE,
         preceding=r"(?:Case(?:\s+(?:No|Number|#))?|Docket(?:\s+(?:No|#))?)\s*[:#]?\s*",
         regex=r"[A-Za-z0-9:-]{4,20}", priority=80, confidence=0.9),

    # --- names (anchored fields are high-confidence; honorifics medium; bigrams low) ---
    Rule("name_labeled", "Name (labeled field)", EntityType.NAME,
         preceding=r"(?:Patient(?:\s+Name)?|Name|Provider|Physician|Attending|Guardian|Client|Plaintiff|Defendant|Emergency Contact|Contact|Referring(?:\s+Provider)?)\s*[:#][ \t]*",
         regex=_NAME_VALUE, priority=78, confidence=0.9, ignore_case=False),
    Rule("name_honorific", "Name (honorific)", EntityType.NAME,
         regex=r"(?:Dr|Mr|Mrs|Ms|Miss|Prof|Rev)\.?[ \t]+" + _NAME_VALUE,
         priority=60, confidence=0.75, ignore_case=False),

    # --- dates (anchored DOB is its own type; free dates default to DATE) ---
    Rule("dob", "Date of birth", EntityType.DOB,
         preceding=r"(?:DOB|D\.O\.B\.|Date of Birth|Birth\s*date)\s*[:#]?\s*",
         regex=_DATE_VALUE, priority=90, confidence=0.95),
    Rule("date", "Date", EntityType.DATE, regex=_DATE_VALUE, priority=70, confidence=0.85),

    # --- clinical quasi-identifiers (preserved by default; detected so policy CAN act) ---
    Rule("age", "Age", EntityType.AGE,
         regex=r"\b(\d{1,3})(?=[-\s]*(?:years?[-\s]*old|yo\b|y/o\b|y\.o\.))",
         priority=65, confidence=0.85),
    Rule("age_labeled", "Age (labeled)", EntityType.AGE,
         preceding=r"Age\s*[:#]\s*", regex=r"\d{1,3}", priority=70, confidence=0.9),
    Rule("sex_labeled", "Sex (labeled)", EntityType.SEX,
         preceding=r"(?:Sex|Gender)\s*[:#]\s*", regex=r"[A-Za-z-]+",
         priority=70, confidence=0.9),
    Rule("sex_term", "Sex (term)", EntityType.SEX, regex=_ci_group(SEX_TERMS),
         priority=40, confidence=0.6),
    Rule("ethnicity", "Ethnicity/Race", EntityType.ETHNICITY, regex=_ci_group(ETHNICITY_TERMS),
         priority=45, confidence=0.7),

    # --- addresses (street line; ZIP handled by the magnitude/zip pass) ---
    Rule("address", "Street address", EntityType.ADDRESS,
         regex=r"\b\d{1,6}\s+(?:[A-Z][A-Za-z.]+\s){1,4}(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Lane|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|Ter)\b\.?",
         priority=75, confidence=0.85),
    # City preceding a state abbrev — Safe Harbor removes geography smaller than a state.
    Rule("city_state", "City (before state)", EntityType.ADDRESS,
         regex=r"[A-Z][a-z]+(?:[ ][A-Z][a-z]+)?",
         succeeding=r",[ ]+(?:" + "|".join(US_STATES) + r")\b",
         priority=76, confidence=0.8, ignore_case=False),
]
