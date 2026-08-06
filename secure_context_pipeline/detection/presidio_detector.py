"""Presidio-backed detector — the intended substrate.

Presidio runs the detection (its NER + built-in recognizers + custom recognizers compiled from
our rules); we add the domain passes Presidio doesn't ship (magnitude rule, clinical
quasi-identifier lexicon, label-driven values) and merge. If Presidio isn't importable, the
factory falls back to the native rule engine so nothing breaks.
"""

from __future__ import annotations

from ..config import ObfuscationPolicy
from ..entities import DetectedEntity, EntityType
from .clustering import assign_clusters
from .rule_engine import RuleEngineDetector

try:  # guarded — Presidio + spaCy model is a heavy, optional install
    from presidio_analyzer import AnalyzerEngine

    _HAS_PRESIDIO = True
except Exception:  # pragma: no cover - import guard
    _HAS_PRESIDIO = False

# Presidio entity label -> our EntityType
_PRESIDIO_MAP = {
    "PERSON": EntityType.NAME,
    "US_SSN": EntityType.SSN,
    "PHONE_NUMBER": EntityType.PHONE,
    "EMAIL_ADDRESS": EntityType.EMAIL,
    "CREDIT_CARD": EntityType.CREDIT_CARD,
    "US_BANK_NUMBER": EntityType.ACCOUNT,
    "LOCATION": EntityType.ADDRESS,
    "DATE_TIME": EntityType.DATE,
    "IP_ADDRESS": EntityType.IP,
    "URL": EntityType.URL,
    "MEDICAL_LICENSE": EntityType.GENERIC_ID,
    "US_DRIVER_LICENSE": EntityType.GENERIC_ID,
}


class PresidioDetector:
    def __init__(self) -> None:
        self._analyzer = AnalyzerEngine()
        self._domain = RuleEngineDetector()  # our domain passes ride alongside Presidio

    async def detect(
        self, text: str, policy: ObfuscationPolicy
    ) -> list[DetectedEntity]:
        ents: list[DetectedEntity] = []
        for r in self._analyzer.analyze(text=text, language="en"):
            et = _PRESIDIO_MAP.get(r.entity_type)
            if et is None:
                continue
            ents.append(DetectedEntity(r.start, r.end, et, text[r.start:r.end], float(r.score), "presidio"))
        # domain passes Presidio doesn't do: magnitude, clinical lexicon, label-driven
        ents += self._domain._magnitude(text)
        ents += self._domain._clinical(text)
        ents += self._domain._label_rules(text)
        assign_clusters(ents)
        return ents


def get_detector(prefer_presidio: bool = True):
    """Presidio substrate when available and initializable; native rule engine otherwise."""
    if prefer_presidio and _HAS_PRESIDIO:
        try:
            return PresidioDetector()
        except Exception:  # pragma: no cover - missing spaCy model, etc.
            pass
    return RuleEngineDetector()
