"""The magnitude rule + ZIP recognizer — the bare-number problem.

A clinical document is full of naked numbers, and the two ways to get this wrong both cause
real harm: scrub a dose or a viral load and you corrupt care; pass an account number and you
leak. Physiology draws the line — nobody doses 15000 mg, so:

* **5+ consecutive digits → scrub** (an identifier, not a physiologic quantity),
* **leading zero → scrub** (identifiers are zero-padded; magnitudes are not),
* **short, separator-free → keep** (a dose, a lab value),
* **unit-adjacency carve-out** — a 5+ run *immediately followed by a clinical unit* is a lab
  value (``100000 copies/mL``, cell counts) and is **kept**. This is what stops the rule from
  eating a viral load.

ZIP (Safe Harbor #17) is recognized here too — a 5-digit run bound to a US state abbreviation —
so it routes through policy like any other identifier rather than being mistaken for a bare
number.
"""

from __future__ import annotations

import re

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .rules import CLINICAL_UNITS, US_STATES

_DIGIT_RUN = re.compile(r"\d+")
_ZIP_RE = re.compile(r"\b(" + "|".join(US_STATES) + r")\s+(\d{5})(?:-\d{4})?\b")
# Optional single space then a clinical unit, right after the number.
_UNIT_AFTER = re.compile(r"\s?(?:" + "|".join(re.escape(u) for u in CLINICAL_UNITS) + r")\b")


def _unit_adjacent(text: str, end: int) -> bool:
    """True if a clinical unit token immediately follows the number ending at ``end``."""
    return _UNIT_AFTER.match(text, end) is not None


def magnitude_spans(text: str, policy: ObfuscationPolicy) -> list[DetectedEntity]:
    """Detect ZIPs and bare-number identifiers via the magnitude rule."""
    spans: list[DetectedEntity] = []

    for m in _ZIP_RE.finditer(text):
        spans.append(
            DetectedEntity(
                start=m.start(2), end=m.end(),  # cover ZIP (+ optional +4), not the state
                entity_type=EntityType.ZIP, text=m.group(0)[m.start(2) - m.start():],
                confidence=0.8, source="magnitude",
            )
        )

    for m in _DIGIT_RUN.finditer(text):
        run = m.group(0)
        length = len(run)
        if _unit_adjacent(text, m.end()):
            continue  # lab value / physiologic magnitude — keep it
        # 5+ digits, or a zero-padded run of 3+ (identifiers are zero-padded; a 2-digit "08"
        # is usually a clock/room number, so we don't scrub those and mangle clinical text).
        scrub = length >= 5 or (run[0] == "0" and length >= 3)
        if scrub:
            spans.append(
                DetectedEntity(
                    start=m.start(), end=m.end(), entity_type=EntityType.GENERIC_ID,
                    text=run, confidence=0.6, source="magnitude",
                )
            )
    return spans
