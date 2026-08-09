"""Generalize — reduce precision instead of substituting. One-way, **no vault entry**.

The Safe Harbor primitive for dates (#3): a date becomes its *year*. The kept year is
**truthful**, so there is nothing to restore — the model only ever sees ``1973`` and can only
reference ``1973``. Dates therefore drop out of de-obfuscation entirely (no grammar match, no
vault lookup, no "computed date" caveat) — the payoff of generalizing rather than substituting.

Age-driven branches (thread the document's ``patient_age`` in):
* ``age >= elderly_age_ceiling`` (default 90) — Safe Harbor requires removing all dates and
  banding age to ``"90+"``, because the >89 cohort is small enough to re-identify on age alone.
* ``age <= pediatric_age_floor`` (default 5) — dates are *preserved* (day-precise dosing is
  clinically load-bearing) and the engine routes the document to a human. A known, accepted
  PHI-handling exception, not a silent pass-through.
"""

from __future__ import annotations

import re

from ...config import AgeMode, ObfuscationPolicy
from ...entities import Action, EntityType
from ...vault.keyring import KeyRing
from .base import Obfuscated, ObfuscationStrategy

# Non-token marker (no ``_hex`` grammar) so the leftover-guard never mistakes it for residue.
_REDACTED = "[redacted]"
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\b")  # plausible calendar year 1800-2099
_AGE_RE = re.compile(r"\d{1,3}")
_ZIP3_RE = re.compile(r"\d{5}")
# HIPAA Safe Harbor #17: 3-digit ZIP prefixes whose population is <20,000 are zeroed to 000.
_RESTRICTED_ZIP3 = {
    "036", "059", "063", "102", "203", "556", "692", "790", "821", "823",
    "830", "831", "878", "879", "884", "890", "893",
}


class Generalize(ObfuscationStrategy):
    action = Action.GENERALIZE

    def apply(
        self,
        canonical: str,
        entity_type: EntityType,
        keyring: KeyRing,
        doc_id: str,
        policy: ObfuscationPolicy,
        *,
        patient_age: int | None = None,
    ) -> Obfuscated:
        if entity_type in (EntityType.DATE, EntityType.DOB):
            return Obfuscated(self._generalize_date(canonical, policy, patient_age))
        if entity_type is EntityType.AGE:
            return Obfuscated(self._generalize_age(canonical, policy))
        if entity_type is EntityType.ZIP:
            return Obfuscated(self._generalize_zip(canonical))
        # Any other type routed here degrades to precision-loss = full redaction (fail closed).
        return Obfuscated(_REDACTED)

    def _generalize_zip(self, canonical: str) -> str:
        m = _ZIP3_RE.search(canonical)
        if not m:
            return _REDACTED
        prefix = m.group(0)[:3]
        return "000XX" if prefix in _RESTRICTED_ZIP3 else prefix + "XX"

    def _generalize_date(
        self, canonical: str, policy: ObfuscationPolicy, patient_age: int | None
    ) -> str:
        if patient_age is not None:
            if patient_age >= policy.elderly_age_ceiling:
                return _REDACTED  # elderly cohort: remove all dates (Safe Harbor)
            if patient_age <= policy.pediatric_age_floor:
                return canonical  # pediatric: preserve; engine flags route-to-human
        m = _YEAR_RE.search(canonical)
        return m.group(1) if m else _REDACTED

    def _generalize_age(self, canonical: str, policy: ObfuscationPolicy) -> str:
        m = _AGE_RE.search(canonical)
        if not m:
            return _REDACTED
        age = int(m.group(0))
        if age >= policy.elderly_age_ceiling:
            return "90+"
        if policy.age_mode is AgeMode.BAND:
            lo = (age // 5) * 5
            return f"{lo}-{lo + 4}"
        return str(age)
