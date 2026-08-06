"""Core typed vocabulary: entity types, data classes, actions, detected spans.

Extensibility (NFR): a new entity type is a new enum member + a detection rule + a routing
entry. No obfuscation/vault/de-obf code changes — they are all entity-type-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DataClass(str, Enum):
    """Regulatory bucket — used for audit classification, never for logic branching."""

    PII = "PII"
    PHI = "PHI"
    LEGAL = "LEGAL"
    FINANCIAL = "FINANCIAL"


class EntityType(str, Enum):
    """What a detected span is. ``.tag`` is what appears in ``[TAG_hex]`` tokens."""

    NAME = "NAME"
    SSN = "SSN"
    DOB = "DOB"
    DATE = "DATE"
    ADDRESS = "ADDRESS"
    ZIP = "ZIP"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    MRN = "MRN"
    ACCOUNT = "ACCOUNT"
    INSURANCE_ID = "INSURANCE_ID"
    CREDIT_CARD = "CREDIT_CARD"
    IP = "IP"
    URL = "URL"
    DIAGNOSIS = "DIAGNOSIS"
    MEDICATION = "MEDICATION"
    # Clinically load-bearing quasi-identifiers — preserved by default (Safe Harbor permits;
    # pharmacogenomics needs them). Obfuscated only if ObfuscationPolicy flips the knob.
    ETHNICITY = "ETHNICITY"
    AGE = "AGE"
    SEX = "SEX"
    # Legal / financial
    LEGAL_CASE = "LEGAL_CASE"
    ACCOUNT_FINANCIAL = "ACCOUNT_FINANCIAL"
    # Catch-all for custom / unknown-but-identifier
    GENERIC_ID = "GENERIC_ID"

    @property
    def tag(self) -> str:
        return self.value


#: Regulatory classification, for the audit trail only.
DATA_CLASS: dict[EntityType, DataClass] = {
    EntityType.NAME: DataClass.PII,
    EntityType.SSN: DataClass.PII,
    EntityType.DOB: DataClass.PII,
    EntityType.DATE: DataClass.PHI,
    EntityType.ADDRESS: DataClass.PII,
    EntityType.ZIP: DataClass.PII,
    EntityType.EMAIL: DataClass.PII,
    EntityType.PHONE: DataClass.PII,
    EntityType.MRN: DataClass.PHI,
    EntityType.ACCOUNT: DataClass.FINANCIAL,
    EntityType.INSURANCE_ID: DataClass.PHI,
    EntityType.CREDIT_CARD: DataClass.FINANCIAL,
    EntityType.IP: DataClass.PII,
    EntityType.URL: DataClass.PII,
    EntityType.DIAGNOSIS: DataClass.PHI,
    EntityType.MEDICATION: DataClass.PHI,
    EntityType.ETHNICITY: DataClass.PHI,
    EntityType.AGE: DataClass.PHI,
    EntityType.SEX: DataClass.PHI,
    EntityType.LEGAL_CASE: DataClass.LEGAL,
    EntityType.ACCOUNT_FINANCIAL: DataClass.FINANCIAL,
    EntityType.GENERIC_ID: DataClass.PII,
}


class Action(str, Enum):
    """What the obfuscation engine does with an entity."""

    TOKENIZE = "TOKENIZE"          # reversible, bias-neutral; identifiers/names
    PSEUDONYMIZE = "PSEUDONYMIZE"  # realistic fake; restore-tolerant contexts only
    GENERALIZE = "GENERALIZE"      # reduce precision (dates->year); one-way, vault-free
    REDACT = "REDACT"             # scrub entirely; graceful degradation / low confidence
    PRESERVE = "PRESERVE"         # leave in place (clinical signal under policy)
    ROUTE_TO_HUMAN = "ROUTE_TO_HUMAN"  # fail closed


@dataclass
class DetectedEntity:
    """A detected span over the ORIGINAL text.

    ``text`` is the original PII value. It lives in memory only and MUST NEVER be logged or
    sent downstream — the audit layer records ``entity_type`` + token, never this field.
    """

    start: int
    end: int
    entity_type: EntityType
    text: str
    confidence: float = 1.0
    source: str = "rules"
    cluster_id: str | None = None  # set by coreference clustering (review finding R1)

    def overlaps(self, other: "DetectedEntity") -> bool:
        return self.start < other.end and other.start < self.end

    @property
    def data_class(self) -> DataClass:
        return DATA_CLASS.get(self.entity_type, DataClass.PII)
